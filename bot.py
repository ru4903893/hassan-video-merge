# bot.py
# Pyrogram Video/Audio Merge Bot (Integrated Full Version)
# NOTE: Fill BOT_TOKEN, API_ID, API_HASH in environment or variables below.

import os
import asyncio
import shlex
import logging
from pathlib import Path
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

logging.basicConfig(level=logging.INFO)

# ---------------- CONFIG ----------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH", "")

app = Client(
    "video_merge_bot",
    bot_token=BOT_TOKEN,
    api_id=API_ID,
    api_hash=API_HASH
)

ROOT = Path.cwd()
WORKDIR = ROOT / "downloads"
THUMBDIR = ROOT / "thumbnails"
WORKDIR.mkdir(exist_ok=True)
THUMBDIR.mkdir(exist_ok=True)

DEFAULT_THUMB = ROOT / "default_thumb.jpg"  # replace if needed
if not DEFAULT_THUMB.exists():
    DEFAULT_THUMB = None

user_state = {}


def q(s):
    return shlex.quote(str(s))


async def run_ffmpeg_cmd(cmd_args, timeout=300):
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd_args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            raise RuntimeError(err.decode())
        return out.decode(), err.decode()
    except Exception as e:
        raise RuntimeError(str(e))


# ---------------- MENU ----------------
MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("🎬 Video + Video", callback_data="m_vv")],
    [InlineKeyboardButton("🎵 Audio + Audio", callback_data="m_aa")],
    [InlineKeyboardButton("🎧 Video + Audio", callback_data="m_va")],
    [InlineKeyboardButton("🖼 Thumbnail Settings", callback_data="thumb_menu")]
])

THUMB_MENU = InlineKeyboardMarkup([
    [InlineKeyboardButton("📤 Upload Thumbnail", callback_data="thumb_upload")],
    [InlineKeyboardButton("❌ Remove Thumbnail", callback_data="thumb_remove")],
    [InlineKeyboardButton("⬅️ Back", callback_data="back_home")]
])


# ---------------- START ----------------
@app.on_message(filters.command("start"))
async def start(_, msg):
    user_state[msg.from_user.id] = {}
    await msg.reply_text(
        "**Welcome to Video Merge Bot**\nChoose an option below:",
        reply_markup=MENU
    )


# ---------------- CALLBACKS ----------------
@app.on_callback_query()
async def callbacks(_, cq):
    uid = cq.from_user.id

    if cq.data == "back_home":
        await cq.message.edit("Choose a mode:", reply_markup=MENU)

    elif cq.data.startswith("m_"):
        mode = cq.data
        user_state[uid] = {"mode": mode, "files": []}
        await cq.message.edit(
            f"Send **2 files** for mode: `{mode}`"
        )

    elif cq.data == "thumb_menu":
        await cq.message.edit("Thumbnail Options:", reply_markup=THUMB_MENU)

    elif cq.data == "thumb_upload":
        user_state.setdefault(uid, {})["await_thumb"] = True
        await cq.message.edit("Send your thumbnail image now.")

    elif cq.data == "thumb_remove":
        thumb = THUMBDIR / f"{uid}.jpg"
        if thumb.exists():
            thumb.unlink()
        await cq.message.edit("Thumbnail removed.", reply_markup=MENU)

    await cq.answer()


# ---------------- RECV FILES ----------------
@app.on_message(filters.private & (filters.video | filters.audio | filters.document))
async def file_receiver(_, msg):
    uid = msg.from_user.id
    state = user_state.get(uid, {})

    # Thumbnail upload
    if state.get("await_thumb"):
        path = THUMBDIR / f"{uid}.jpg"
        await msg.download(path)
        user_state[uid]["await_thumb"] = False
        await msg.reply_text("Thumbnail saved.", reply_markup=MENU)
        return

    # Merge mode
    if "mode" not in state:
        return await msg.reply_text("Please select a mode first.")

    # Save incoming file
    dl_path = WORKDIR / f"{uid}_{msg.id}"
    file_path = await msg.download(dl_path)
    state["files"].append(Path(file_path))

    # If 2 files received → start merging
    if len(state["files"]) == 2:
        await merge_files(msg, uid)
        user_state[uid] = {}


# ---------------- MERGE LOGIC ----------------
async def merge_files(msg, uid):
    mode = user_state[uid]["mode"]
    f1, f2 = user_state[uid]["files"]
    out = WORKDIR / f"merged_{uid}.mp4"

    thumb = THUMBDIR / f"{uid}.jpg"
    thumb = thumb if thumb.exists() else DEFAULT_THUMB

    await msg.reply("🔄 Processing your files...")

    try:
        if mode == "m_vv":  # Video + Video
            listfile = WORKDIR / f"list_{uid}.txt"
            listfile.write_text(f"file '{f1}'\nfile '{f2}'\n")
            cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile), "-c", "copy", str(out)]

        elif mode == "m_aa":  # Audio + Audio
            listfile = WORKDIR / f"list_{uid}.txt"
            listfile.write_text(f"file '{f1}'\nfile '{f2}'\n")
            cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile), "-c", "copy", str(out)]

        elif mode == "m_va":  # Video + Audio
            cmd = ["ffmpeg", "-y", "-i", str(f1), "-i", str(f2), "-c:v", "copy", "-c:a", "aac", str(out)]

        await run_ffmpeg_cmd(cmd)

        # Send with thumbnail if exists
        if thumb:
            await msg.reply_video(str(out), thumb=str(thumb))
        else:
            await msg.reply_video(str(out))

    except Exception as e:
        await msg.reply_text(f"❌ Error: {e}")

    finally:
        # Cleanup
        try:
            if out.exists(): pass
            for f in user_state[uid]["files"]:
                if f.exists(): f.unlink()
        except:
            pass


# ---------------- RUN ----------------
app.run()
