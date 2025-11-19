# Video Merge Bot (Pyrogram) - bot.py
"""
Production-ready (starter) Pyrogram bot script: bot.py
Features:
- Menu UI with InlineButtons (like the screenshot)
- Modes: Video+Video, Audio+Audio, Video+Audio
- Per-user thumbnail support (set via menu)
- Basic validation and cleanup
- Uses ffmpeg (system ffmpeg required)

Place this file as bot.py in your repo. Make sure to set the environment variables:
- BOT_TOKEN
- API_ID
- API_HASH

Notes:
- I included a default sample thumbnail path (uploaded image) at: /mnt/data/1000216109.jpg
  You can remove or replace it. When deployed, users will upload their own thumbnails.

"""

import os
import shlex
import asyncio
import logging
from pathlib import Path
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

logging.basicConfig(level=logging.INFO)

# --- Configuration ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = int(os.getenv("API_ID", "0"))
API_HASH = os.getenv("API_HASH")

if not BOT_TOKEN or not API_ID or not API_HASH:
    logging.warning("BOT_TOKEN/API_ID/API_HASH are not all set. Fill environment variables before running.")

app = Client("video_merge_bot", bot_token=BOT_TOKEN, api_id=API_ID, api_hash=API_HASH)

ROOT = Path.cwd()
WORKDIR = ROOT / "downloads"
THUMBDIR = ROOT / "thumbnails"
WORKDIR.mkdir(exist_ok=True)
THUMBDIR.mkdir(exist_ok=True)

# Default thumbnail (uploaded image included in repo). Developer provided path:
DEFAULT_THUMB = Path("/mnt/data/1000216109.jpg")
if not DEFAULT_THUMB.exists():
    DEFAULT_THUMB = None

# In-memory per-user state
user_state = {}

# ---------- Helpers ----------

def q(s: str) -> str:
    """Quote a path for shell safely."""
    return shlex.quote(str(s))

async def run_cmd(cmd: str) -> None:
    logging.info("Running command: %s", cmd)
    proc = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    if proc.returncode != 0:
        logging.error("Command failed: %s", err.decode(errors="ignore"))
        raise RuntimeError(err.decode(errors="ignore"))
    return out.decode(errors="ignore")

# ---------- UI ----------

def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📹 Video + Video", callback_data="v_v")],
        [InlineKeyboardButton("🔊 Audio + Audio", callback_data="a_a")],
        [InlineKeyboardButton("🎧 Video + Audio", callback_data="v_a")],
        [InlineKeyboardButton("🖼️ Set Thumbnail", callback_data="set_thumb")],
        [InlineKeyboardButton("📁 Show Thumbnail", callback_data="show_thumb"), InlineKeyboardButton("❌ Delete Thumb", callback_data="del_thumb")],
        [InlineKeyboardButton("❓ Help", callback_data="help")]
    ])

# ---------- Commands ----------

@app.on_message(filters.command("start"))
async def start_cmd(c, m):
    text = "Welcome! Choose an option to begin. You can set a custom thumbnail first if you want."
    await m.reply_text(text, reply_markup=main_keyboard())

# ---------- Callback handler ----------

@app.on_callback_query()
async def callbacks(c, cb):
    uid = cb.from_user.id
    data = cb.data

    if data in ("v_v", "a_a", "v_a"):
        user_state[uid] = {"mode": data, "files": []}
        await cb.message.edit_text(f"Selected mode: {data.replace('_',' + ')}
Now send the FIRST file (video/audio as appropriate).", reply_markup=None)
        await cb.answer()
        return

    if data == "set_thumb":
        user_state[uid] = user_state.get(uid, {})
        user_state[uid]["expect_thumb"] = True
        await cb.message.edit_text("Send an image (photo) to set as your custom thumbnail.")
        await cb.answer()
        return

    if data == "show_thumb":
        thumb = THUMBDIR / f"{uid}.jpg"
        if thumb.exists():
            await cb.message.reply_photo(str(thumb), caption="Your thumbnail")
        elif DEFAULT_THUMB:
            await cb.message.reply_photo(str(DEFAULT_THUMB), caption="Default thumbnail (no custom thumbnail set)")
        else:
            await cb.message.reply_text("No thumbnail set.")
        await cb.answer()
        return

    if data == "del_thumb":
        thumb = THUMBDIR / f"{uid}.jpg"
        if thumb.exists():
            thumb.unlink()
            await cb.message.edit_text("Thumbnail deleted.")
        else:
            await cb.message.edit_text("No thumbnail to delete.")
        await cb.answer()
        return

    if data == "help":
        await cb.message.edit_text("Help:
1) Choose a mode.
2) Send First file.
3) Send Second file.
4) Bot will merge and send back the result.
Set thumbnail via 'Set Thumbnail'.")
        await cb.answer()
        return

# ---------- File handler ----------

@app.on_message(filters.video | filters.audio | filters.photo | filters.document)
async def files_handler(c, m):
    uid = m.from_user.id
    state = user_state.get(uid, {})

    # Thumbnail flow
    if state.get("expect_thumb"):
        if m.photo:
            path = THUMBDIR / f"{uid}.jpg"
            await m.download(file_name=str(path))
            state.pop("expect_thumb", None)
            user_state[uid] = state
            await m.reply_text("Thumbnail saved ✔️", reply_markup=main_keyboard())
            return
        else:
            await m.reply_text("Please send a photo to set as thumbnail.")
            return

    mode = state.get("mode")
    if not mode:
        await m.reply_text("Choose a mode first from the menu. Use /start to open the menu.")
        return

    # Accept video/audio/document
    # Save file
    save_path = WORKDIR / f"{uid}_{m.message_id}"
    # Choose extension
    file_name = await m.download(file_name=str(save_path))
    file_path = Path(file_name)

    # Keep track
    state.setdefault("files", []).append(str(file_path))
    user_state[uid] = state

    files = state["files"]
    if len(files) == 1:
        await m.reply_text("First file received. Now send the second file.")
        return

    # We have two files -> process
    await m.reply_text("Merging files… This may take a while.")

    f1 = Path(files[0])
    f2 = Path(files[1])

    out_path = WORKDIR / f"{uid}_merged"

    try:
        if mode == "v_v":
            # For video+video: re-encode compatible streams via concat demuxer
            # Create intermediate txt list
            list_txt = WORKDIR / f"{uid}_list.txt"
            list_txt.write_text(f"file {q(f1)}
file {q(f2)}
")
            out_path = out_path.with_suffix('.mp4')
            cmd = f"ffmpeg -y -f concat -safe 0 -i {q(list_txt)} -c copy {q(out_path)}"
            # Some containers won't concat with -c copy; fallback to re-encode
            try:
                await run_cmd(cmd)
            except Exception:
                # Re-encode fallback
                cmd2 = f"ffmpeg -y -i {q(f1)} -i {q(f2)} -filter_complex \"[0:v]scale=trunc(iw/2)*2:trunc(ih/2)*2[v0]; [1:v]scale=trunc(iw/2)*2:trunc(ih/2)*2[v1]; [v0][0:a][v1][1:a]concat=n=2:v=1:a=1[v][a]\" -map \"[v]\" -map \"[a]\" {q(out_path)}"
                await run_cmd(cmd2)

        elif mode == "a_a":
            out_path = out_path.with_suffix('.mp3')
            # Use concat filter for audio
            cmd = f"ffmpeg -y -i {q(f1)} -i {q(f2)} -filter_complex 'concat=n=2:v=0:a=1' -vn {q(out_path)}"
            await run_cmd(cmd)

        elif mode == "v_a":
            # Expect first file video, second file audio (or vice versa). We'll attach audio2 to video1.
            out_path = out_path.with_suffix('.mp4')
            # If first is audio and second video, swap
            if m.mime_type and m.mime_type.startswith('audio/'):
                video_file = f2
                audio_file = f1
            else:
                video_file = f1
                audio_file = f2
            cmd = f"ffmpeg -y -i {q(video_file)} -i {q(audio_file)} -map 0:v -map 1:a -c:v copy -shortest {q(out_path)}"
            try:
                await run_cmd(cmd)
            except Exception:
                # Fallback: re-encode video
                cmd2 = f"ffmpeg -y -i {q(video_file)} -i {q(audio_file)} -map 0:v -map 1:a -c:v libx264 -c:a aac -shortest {q(out_path)}"
                await run_cmd(cmd2)
        else:
            raise RuntimeError("Unknown mode")

        # Determine thumbnail
        thumb = THUMBDIR / f"{uid}.jpg"
        if not thumb.exists() and DEFAULT_THUMB:
            thumb = DEFAULT_THUMB
        elif not thumb.exists():
            thumb = None

        # Send file
        if out_path.exists():
            # If result is video or audio, use send_document to preserve file size
            await m.reply_document(str(out_path), thumb=str(thumb) if thumb else None)
        else:
            await m.reply_text("Merging failed: output file not found.")

    except Exception as e:
        logging.exception("Error during merge")
        await m.reply_text(f"Error while processing: {e}")

    finally:
        # cleanup user files
        for p in (f1, f2, WORKDIR / f"{uid}_list.txt"):
            try:
                if p.exists():
                    p.unlink()
            except Exception:
                pass
        # reset state
        user_state.pop(uid, None)

# ---------- Run ----------

if __name__ == '__main__':
    print("Starting Video Merge Bot...")
    app.run()
