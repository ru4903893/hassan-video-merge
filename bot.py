# bot.py
"""
Hassan Video Merge Bot - bot.py

Features included:
- Inline menu: thumbnail, metadata, plan, help, about, delete/show thumbnail
- Thumbnail set/show/delete
- Metadata set/show/delete
- Merge flows:
    /merge_vv  -> video + video (concatenate sequentially)
    /merge_aa  -> audio + audio (mix using amix)
    /merge_va  -> video + audio (replace video's audio with provided audio)
Usage:
- Reply to the FIRST media with the merge command (e.g. reply to first video with /merge_vv).
- Bot will ask you to send the SECOND media (same chat, from same user).
- Send/Reply the SECOND media and bot will process and return merged file.
"""

import os
import asyncio
import shlex
import json
import logging
import uuid
import time
import shutil

from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message

# ---------- CONFIG ----------
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
OWNER_ID = int(os.environ.get("OWNER_ID", "0")) if os.environ.get("OWNER_ID") else None

if not (API_ID and API_HASH and BOT_TOKEN):
    raise SystemExit("Please set API_ID, API_HASH and BOT_TOKEN environment variables.")

SESSION_NAME = "hassan_merge_bot"
DATA_DIR = "data"
THUMB_PATH = os.path.join(DATA_DIR, "thumb.jpg")
META_PATH = os.path.join(DATA_DIR, "meta.json")
TMP_DIR = os.path.join(DATA_DIR, "tmp")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(TMP_DIR, exist_ok=True)

# ---------- logging ----------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger(__name__)

# ---------- pyrogram client ----------
app = Client(SESSION_NAME, api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# ---------- in-memory per-chat state ----------
# persistent production system should use DB; this is simple and ephemeral.
pending = {}  # chat_id -> { "action": str, "owner": user_id, "first_file": path, "first_type": "video"/"audio", "ts": timestamp }

# ---------- Helpers ----------
async def run_cmd(cmd: str, cwd: str = None, timeout: int = 600):
    """
    Run shell command asynchronously and return (returncode, stdout, stderr).
    """
    log.info("Run cmd: %s", cmd)
    try:
        proc = await asyncio.create_subprocess_shell(
            cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return -1, "", "Timeout"
        return proc.returncode, out.decode(errors="ignore"), err.decode(errors="ignore")
    except Exception as e:
        return -1, "", str(e)

def clean_tmp(folder=TMP_DIR):
    """Remove all files in tmp folder older than some threshold (safety)."""
    now = time.time()
    for fname in os.listdir(folder):
        fpath = os.path.join(folder, fname)
        try:
            if os.path.isfile(fpath) and (now - os.path.getmtime(fpath) > 60*60):  # 1 hour
                os.remove(fpath)
        except Exception:
            pass

async def download_media_to_path(msg: Message, dest_path: str):
    """
    Download attached media (video/audio/document/photo) to dest_path.
    Returns True on success.
    """
    try:
        await msg.download(file_name=dest_path)
        return True
    except Exception as e:
        log.exception("download error: %s", e)
        return False

def save_meta(title: str, caption: str):
    with open(META_PATH, "w", encoding="utf-8") as f:
        json.dump({"title": title, "caption": caption}, f, ensure_ascii=False, indent=2)

def load_meta():
    if not os.path.exists(META_PATH):
        return None
    with open(META_PATH, "r", encoding="utf-8") as f:
        return json.load(f)

def now_ts():
    return int(time.time())

# ---------- Inline Menus ----------
MAIN_MENU = InlineKeyboardMarkup(
    [
        [InlineKeyboardButton("📁 Thumbnail", callback_data="menu_thumb")],
        [InlineKeyboardButton("📝 Metadata", callback_data="menu_meta"),
         InlineKeyboardButton("💳 Plan", callback_data="menu_plan")],
        [InlineKeyboardButton("❓ Help", callback_data="menu_help"),
         InlineKeyboardButton("ℹ️ About", callback_data="menu_about")],
    ]
)

THUMB_MENU = InlineKeyboardMarkup(
    [
        [InlineKeyboardButton("➕ Set thumbnail", callback_data="thumb_set"),
         InlineKeyboardButton("👁️ Show thumbnail", callback_data="thumb_show")],
        [InlineKeyboardButton("🗑️ Delete thumbnail", callback_data="thumb_del"),
         InlineKeyboardButton("◀️ Back", callback_data="menu_back")]
    ]
)

META_MENU = InlineKeyboardMarkup(
    [
        [InlineKeyboardButton("✏️ Set metadata", callback_data="meta_set"),
         InlineKeyboardButton("👁️ Show metadata", callback_data="meta_show")],
        [InlineKeyboardButton("🗑️ Delete metadata", callback_data="meta_del"),
         InlineKeyboardButton("◀️ Back", callback_data="menu_back")]
    ]
)

# ---------- Start ----------
@app.on_message(filters.command("start") & filters.private)
async def start_cmd(_, m: Message):
    txt = "হ্যালো! আমি Video Merge Bot — নিচের মেনু থেকে বেছে নাও।\n\nমিডিয়া ম্যানিপুলেশন করতে হলে প্রথম ফাইলটিতে reply করে merge কমান্ড টা চালাও (নীচে দেখানো)।"
    await m.reply_text(txt, reply_markup=MAIN_MENU)

# ---------- Callback query handler ----------
@app.on_callback_query()
async def cb_handler(_, cq):
    data = cq.data or ""
    uid = cq.from_user.id
    if data == "menu_thumb":
        await cq.message.edit_text("Thumbnail মেনু:", reply_markup=THUMB_MENU)
    elif data == "thumb_set":
        # set pending state for this user in this chat
        pending[cq.message.chat.id] = {"action": "set_thumb", "owner": uid, "ts": now_ts()}
        await cq.answer("এখন একটি ছবি সেন্ড করো — সেট করা হবে থাম্বনেইল হিসেবে.", show_alert=True)
    elif data == "thumb_show":
        if os.path.exists(THUMB_PATH):
            await cq.message.reply_photo(THUMB_PATH, caption="Current thumbnail")
        else:
            await cq.answer("No thumbnail set.", show_alert=True)
    elif data == "thumb_del":
        if os.path.exists(THUMB_PATH):
            os.remove(THUMB_PATH)
            await cq.answer("Thumbnail deleted.", show_alert=True)
        else:
            await cq.answer("No thumbnail to delete.", show_alert=True)
    elif data == "menu_meta":
        await cq.message.edit_text("Metadata মেনু:", reply_markup=META_MENU)
    elif data == "meta_set":
        pending[cq.message.chat.id] = {"action": "set_meta", "owner": uid, "ts": now_ts()}
        await cq.answer("Use /setmeta Title|Caption or reply to a message with /setmeta.", show_alert=True)
    elif data == "meta_show":
        meta = load_meta()
        if meta:
            await cq.answer(f"Title: {meta.get('title','')}\nCaption: {meta.get('caption','')}", show_alert=True)
        else:
            await cq.answer("No metadata saved.", show_alert=True)
    elif data == "meta_del":
        if os.path.exists(META_PATH):
            os.remove(META_PATH)
            await cq.answer("Metadata deleted.", show_alert=True)
        else:
            await cq.answer("No metadata to delete.", show_alert=True)
    elif data == "menu_plan":
        await cq.message.edit_text("Plans:\n• Free — basic merging\n• Pro — faster queue\nContact @your_username to upgrade.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="menu_back")]]))
    elif data == "menu_help":
        help_text = (
            "Help:\n"
            "• /merge_vv — Reply to 1st video with this command, then send 2nd video.\n"
            "• /merge_aa — Reply to 1st audio with this command, then send 2nd audio.\n"
            "• /merge_va — Reply to video with this command, then send audio to replace.\n"
            "• Thumbnail: set/show/delete via menu.\n"
        )
        await cq.message.edit_text(help_text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="menu_back")]]))
    elif data == "menu_about":
        await cq.message.edit_text("About: Hassan Video Merge Bot\nDeveloper: You\nVersion: 1.0", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="menu_back")]]))
    elif data == "menu_back":
        await cq.message.edit_text("Main menu:", reply_markup=MAIN_MENU)
    else:
        await cq.answer("Unknown action.")

# ---------- Photo handler (for setting thumbnail) ----------
@app.on_message(filters.photo & filters.private)
async def photo_handler(_, m: Message):
    chat_id = m.chat.id
    state = pending.get(chat_id)
    if state and state.get("action") == "set_thumb" and state.get("owner") == m.from_user.id:
        # download as thumb.jpg
        try:
            await m.download(file_name=THUMB_PATH)
            pending.pop(chat_id, None)
            await m.reply_text("✅ Thumbnail saved!", quote=True)
        except Exception as e:
            log.exception("thumb save error")
            await m.reply_text("Failed to save thumbnail: " + str(e), quote=True)

# ---------- Metadata command ----------
@app.on_message(filters.command("setmeta") & filters.private)
async def setmeta_cmd(_, m: Message):
    # Accept: /setmeta Title|Caption
    text = m.text or ""
    parts = text.split(None, 1)
    if len(parts) < 2:
        # Maybe user replied to a message with caption text
        await m.reply_text("Use: /setmeta Title|Caption  (or reply to a message with /setmeta and write Title|Caption as caption).")
        return
    payload = parts[1]
    if "|" in payload:
        title, caption = payload.split("|", 1)
    else:
        title, caption = payload, ""
    save_meta(title.strip(), caption.strip())
    await m.reply_text("✅ Metadata saved.")

@app.on_message(filters.command("showmeta") & filters.private)
async def showmeta_cmd(_, m: Message):
    meta = load_meta()
    if meta:
        await m.reply_text(f"Title: {meta.get('title','')}\nCaption: {meta.get('caption','')}")
    else:
        await m.reply_text("No metadata saved.")

@app.on_message(filters.command("delmeta") & filters.private)
async def delmeta_cmd(_, m: Message):
    if os.path.exists(META_PATH):
        os.remove(META_PATH)
        await m.reply_text("Metadata deleted.")
    else:
        await m.reply_text("No metadata to delete.")

# ---------- Merge flows ----------
# Helper to prepare unique temp paths
def tmp_path(prefix: str, ext: str):
    return os.path.join(TMP_DIR, f"{prefix}_{uuid.uuid4().hex}.{ext}")

# Clean up old tmp files periodically (light)
@app.on_message(filters.private)
async def cleanup_trigger(_, m: Message):
    # occasionally run cleanup
    if int(time.time()) % 50 == 0:
        clean_tmp()

# Start merge command (reply to first file)
@app.on_message(filters.command("merge_vv") & filters.reply & filters.private)
async def merge_vv_start(_, m: Message):
    first = m.reply_to_message
    if not first:
        await m.reply_text("Reply to the FIRST video with /merge_vv")
        return
    # verify media
    if not (first.video or (first.document and first.document.mime_type and "video" in (first.document.mime_type))):
        await m.reply_text("Reply to a video file (first) with /merge_vv")
        return
    # download first
    f1 = tmp_path("v1", "mp4")
    ok = await download_media_to_path(first, f1)
    if not ok:
        await m.reply_text("Failed to download first video.")
        return
    chat_id = m.chat.id
    pending[chat_id] = {"action": "merge_vv_wait_second", "owner": m.from_user.id, "first_file": f1, "first_type": "video", "ts": now_ts()}
    await m.reply_text("First video saved. এখন SECOND video পাঠাও (একই চ্যাটে)।")

@app.on_message(filters.command("merge_aa") & filters.reply & filters.private)
async def merge_aa_start(_, m: Message):
    first = m.reply_to_message
    if not first:
        await m.reply_text("Reply to the FIRST audio with /merge_aa")
        return
    # allow audio or voice or document with audio mime
    ok_type = False
    if first.audio or first.voice:
        ok_type = True
    if first.document and first.document.mime_type and first.document.mime_type.startswith("audio"):
        ok_type = True
    if not ok_type:
        await m.reply_text("Reply to an audio/voice file with /merge_aa")
        return
    f1 = tmp_path("a1", "mp3")
    # download
    ok = await download_media_to_path(first, f1)
    if not ok:
        await m.reply_text("Failed to download first audio.")
        return
    chat_id = m.chat.id
    pending[chat_id] = {"action": "merge_aa_wait_second", "owner": m.from_user.id, "first_file": f1, "first_type": "audio", "ts": now_ts()}
    await m.reply_text("First audio saved. এখন SECOND audio পাঠাও (একই চ্যাটে)।")

@app.on_message(filters.command("merge_va") & filters.reply & filters.private)
async def merge_va_start(_, m: Message):
    first = m.reply_to_message
    if not first:
        await m.reply_text("Reply to the video with /merge_va")
        return
    if not (first.video or (first.document and first.document.mime_type and "video" in first.document.mime_type)):
        await m.reply_text("Reply to a video file with /merge_va")
        return
    fvideo = tmp_path("v", "mp4")
    ok = await download_media_to_path(first, fvideo)
    if not ok:
        await m.reply_text("Failed to download video.")
        return
    chat_id = m.chat.id
    pending[chat_id] = {"action": "merge_va_wait_audio", "owner": m.from_user.id, "first_file": fvideo, "first_type": "video", "ts": now_ts()}
    await m.reply_text("Video saved. এখন AUDIO পাঠাও যাতে ভিডিওর অডিও রেপ্লেস করব।")

# Handler for receiving second media (generic)
@app.on_message(filters.private & (filters.video | filters.audio | filters.voice | filters.document | filters.audio))
async def second_media_handler(_, m: Message):
    chat_id = m.chat.id
    state = pending.get(chat_id)
    if not state:
        # nothing expected
        return
    if state.get("owner") != m.from_user.id:
        # not the owner; ignore
        return
    action = state.get("action")
    # video+video second
    if action == "merge_vv_wait_second":
        # ensure the incoming message contains a video
        if not (m.video or (m.document and m.document.mime_type and "video" in (m.document.mime_type or ""))):
            await m.reply_text("Please send a video file as the SECOND file for merging.")
            return
        f2 = tmp_path("v2", "mp4")
        ok = await download_media_to_path(m, f2)
        if not ok:
            await m.reply_text("Failed to download second video.")
            return
        await m.reply_text("Got second video — merging now (may take some time)...")
        out = tmp_path("out_merge", "mp4")
        # Use ffmpeg filter_complex concat (works even if codecs differ, but re-encodes)
        # Build command
        cmd = (
            f"ffmpeg -y -i {shlex.quote(state['first_file'])} -i {shlex.quote(f2)} "
            f"-filter_complex \"[0:v:0][0:a:0][1:v:0][1:a:0]concat=n=2:v=1:a=1[outv][outa]\" "
            f"-map \"[outv]\" -map \"[outa]\" -preset veryfast -movflags +faststart {shlex.quote(out)}"
        )
        code, outp, err = await run_cmd(cmd)
        # cleanup first & second
        try:
            if os.path.exists(state['first_file']): os.remove(state['first_file'])
            if os.path.exists(f2): os.remove(f2)
        except Exception:
            pass
        pending.pop(chat_id, None)
        if code == 0 and os.path.exists(out):
            # send with thumbnail if set
            caption = ""
            meta = load_meta()
            if meta:
                caption = f"{meta.get('title','')}\n\n{meta.get('caption','')}"
            try:
                if os.path.exists(THUMB_PATH):
                    await m.reply_video(out, caption=caption or None, thumb=THUMB_PATH)
                else:
                    await m.reply_video(out, caption=caption or None)
            except Exception as e:
                log.exception("send merged video error")
                await m.reply_text("Merged but failed to send: " + str(e))
            try:
                os.remove(out)
            except Exception:
                pass
        else:
            await m.reply_text("Merge failed:\n" + (err or outp or "Unknown error"))
        return

    # audio+audio second
    if action == "merge_aa_wait_second":
        # check for audio-like: audio, voice, document with audio mime
        ok_type = False
        if m.audio or m.voice:
            ok_type = True
        if m.document and m.document.mime_type and m.document.mime_type.startswith("audio"):
            ok_type = True
        if not ok_type:
            await m.reply_text("Please send an audio/voice file as the SECOND audio.")
            return
        f2 = tmp_path("a2", "mp3")
        ok = await download_media_to_path(m, f2)
        if not ok:
            await m.reply_text("Failed to download second audio.")
            return
        await m.reply_text("Got second audio — mixing now...")
        out = tmp_path("out_mix", "mp3")
        # Use amix to mix two audios
        cmd = (
            f"ffmpeg -y -i {shlex.quote(state['first_file'])} -i {shlex.quote(f2)} "
            f"-filter_complex \"amix=inputs=2:duration=longest:dropout_transition=2\" -c:a libmp3lame -q:a 4 {shlex.quote(out)}"
        )
        code, outp, err = await run_cmd(cmd)
        try:
            if os.path.exists(state['first_file']): os.remove(state['first_file'])
            if os.path.exists(f2): os.remove(f2)
        except Exception:
            pass
        pending.pop(chat_id, None)
        if code == 0 and os.path.exists(out):
            try:
                await m.reply_audio(out, caption="Mixed audio")
            except Exception as e:
                log.exception("send mixed audio error")
                await m.reply_text("Mixed but failed to send: " + str(e))
            try:
                os.remove(out)
            except Exception:
                pass
        else:
            await m.reply_text("Mix failed:\n" + (err or outp or "Unknown error"))
        return

    # video + audio (replace audio)
    if action == "merge_va_wait_audio":
        # ensure incoming is audio-like
        ok_type = False
        if m.audio or m.voice:
            ok_type = True
        if m.document and m.document.mime_type and m.document.mime_type.startswith("audio"):
            ok_type = True
        if not ok_type:
            await m.reply_text("Please send an audio file to replace the video's audio.")
            return
        fa = tmp_path("a_replace", "mp3")
        ok = await download_media_to_path(m, fa)
        if not ok:
            await m.reply_text("Failed to download audio.")
            return
        await m.reply_text("Got audio — replacing video audio now...")
        out = tmp_path("out_va", "mp4")
        # replace audio: copy video stream, map new audio
        cmd = (
            f"ffmpeg -y -i {shlex.quote(state['first_file'])} -i {shlex.quote(fa)} "
            f"-c:v copy -map 0:v:0 -map 1:a:0 -shortest {shlex.quote(out)}"
        )
        code, outp, err = await run_cmd(cmd)
        try:
            if os.path.exists(state['first_file']): os.remove(state['first_file'])
            if os.path.exists(fa): os.remove(fa)
        except Exception:
            pass
        pending.pop(chat_id, None)
        if code == 0 and os.path.exists(out):
            caption = ""
            meta = load_meta()
            if meta:
                caption = f"{meta.get('title','')}\n\n{meta.get('caption','')}"
            try:
                if os.path.exists(THUMB_PATH):
                    await m.reply_video(out, caption=caption or None, thumb=THUMB_PATH)
                else:
                    await m.reply_video(out, caption=caption or None)
            except Exception as e:
                log.exception("send va output error")
                await m.reply_text("Processed but failed to send: " + str(e))
            try:
                os.remove(out)
            except Exception:
                pass
        else:
            await m.reply_text("Processing failed:\n" + (err or outp or "Unknown error"))
        return

# ---------- Manual cancel pending ----------
@app.on_message(filters.command("cancel") & filters.private)
async def cancel_pending(_, m: Message):
    chat_id = m.chat.id
    if chat_id in pending:
        # remove first_file if exists
        try:
            f = pending[chat_id].get("first_file")
            if f and os.path.exists(f):
                os.remove(f)
        except Exception:
            pass
        pending.pop(chat_id, None)
        await m.reply_text("Pending action cancelled.")
    else:
        await m.reply_text("No pending action.")

# ---------- Simple status/admin ----------
@app.on_message(filters.command("status") & filters.private)
async def status_cmd(_, m: Message):
    chat_id = m.chat.id
    st = pending.get(chat_id)
    if st:
        await m.reply_text(f"Pending: {st.get('action')} (owner: {st.get('owner')})")
    else:
        await m.reply_text("No pending action.")

# ---------- Graceful stop (owner only) ----------
@app.on_message(filters.command("stop") & filters.user(OWNER_ID) & filters.private)
async def stop_bot(_, m: Message):
    await m.reply_text("Stopping...")
    await app.stop()

# ---------- Run ----------
if __name__ == "__main__":
    log.info("Starting Hassan Video Merge Bot...")
    app.run()
