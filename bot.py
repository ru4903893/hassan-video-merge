import os
import asyncio
import subprocess
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import FSInputFile

TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=TOKEN)
dp = Dispatcher()

if not os.path.exists("temp"):
    os.makedirs("temp")

user_files = {}
user_mode = {}  # merge mode tracking


@dp.message(Command("start"))
async def start(msg: types.Message):
    kb = InlineKeyboardBuilder()
    kb.button(text="▶ Normal Merge", callback_data="normal")
    kb.button(text="🔳 Side by Side", callback_data="side")
    kb.button(text="🔲 Vertical Stack", callback_data="vertical")
    kb.adjust(1)

    await msg.reply(
        "🎬 *Video Merge Bot Pro*\n\n"
        "দুই বা বেশি ভিডিও পাঠান, তারপর merge মোড বাছাই করুন।",
        parse_mode="Markdown",
        reply_markup=kb.as_markup()
    )


@dp.callback_query()
async def merge_mode(callback: types.CallbackQuery):
    user_mode[callback.from_user.id] = callback.data
    await callback.answer("Mode selected!")
    await callback.message.reply("✔ Merge mode selected!\n\nএখন ভিডিও পাঠাতে থাকুন…")


@dp.message(lambda msg: msg.video)
async def video_received(msg: types.Message):
    user_id = msg.from_user.id
    file = await bot.get_file(msg.video.file_id)

    filename = f"temp/{user_id}_{len(user_files.get(user_id, []))}.mp4"
    await bot.download_file(file.file_path, filename)

    user_files.setdefault(user_id, []).append(filename)
    count = len(user_files[user_id])

    await msg.reply(f"📥 {count}টি ভিডিও পাওয়া গেছে!\n"
                    f"যখন merge করতে চান /merge লিখুন")


@dp.message(Command("merge"))
async def merge_command(msg: types.Message):
    user_id = msg.from_user.id
    files = user_files.get(user_id, [])

    if len(files) < 2:
        return await msg.reply("❗ কমপক্ষে ২টি ভিডিও লাগবে।")

    mode = user_mode.get(user_id, "normal")

    await msg.reply("⏳ Merge চলছে… দয়া করে অপেক্ষা করুন")

    output = f"temp/{user_id}_merged.mp4"
    await merge_videos(files, output, mode)

    await msg.reply_video(
        FSInputFile(output),
        caption="🎉 Merge completed!"
    )

    # clean
    for f in files:
        os.remove(f)
    os.remove(output)
    user_files[user_id] = []


async def merge_videos(files, output, mode):
    txt = f"temp/list.txt"
    with open(txt, "w") as f:
        for file in files:
            f.write(f"file '{file}'\n")

    if mode == "normal":
        cmd = f'ffmpeg -f concat -safe 0 -i {txt} -c copy {output} -y'

    elif mode == "side":
        cmd = f'ffmpeg -i {files[0]} -i {files[1]} -filter_complex "[0:v][1:v]hstack=inputs=2[v]" -map "[v]" {output} -y'

    elif mode == "vertical":
        cmd = f'ffmpeg -i {files[0]} -i {files[1]} -filter_complex "[0:v][1:v]vstack=inputs=2[v]" -map "[v]" {output} -y'

    else:
        cmd = f'ffmpeg -f concat -safe 0 -i {txt} -c copy {output} -y'

    subprocess.call(cmd, shell=True)
    os.remove(txt)


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
