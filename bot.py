import os
import asyncio
import subprocess
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import FSInputFile

TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=TOKEN)
dp = Dispatcher()

# অস্থায়ী ফাইল রাখার ফোল্ডার তৈরি
if not os.path.exists("temp"):
    os.makedirs("temp")

user_files = {}  # প্রতিটা ইউজারের ভিডিও ধরে রাখবে


@dp.message(Command("start"))
async def start(msg: types.Message):
    await msg.reply("🎬 ভিডিও মার্জ বট প্রস্তুত!\n\n"
                    "দুইটি ভিডিও পাঠাও — আমি merge করে দিবো।")


@dp.message(lambda msg: msg.video)
async def get_video(msg: types.Message):
    user_id = msg.from_user.id
    file = await bot.get_file(msg.video.file_id)

    filename = f"temp/{user_id}_{len(user_files.get(user_id, []))}.mp4"
    await bot.download_file(file.file_path, filename)

    user_files.setdefault(user_id, []).append(filename)

    if len(user_files[user_id]) == 1:
        await msg.reply("প্রথম ভিডিও পেলাম! এবার দ্বিতীয় ভিডিও পাঠান…")
    elif len(user_files[user_id]) == 2:
        await msg.reply("✔ দুইটি ভিডিও পেয়েছি!\n⏳ এখন merge করা হচ্ছে…")
        await merge_and_send(msg, user_id)


async def merge_and_send(msg, user_id):
    v1, v2 = user_files[user_id]
    output = f"temp/{user_id}_merged.mp4"

    # ffmpeg merge command
    cmd = f'ffmpeg -i "{v1}" -i "{v2}" -filter_complex "[0:v][1:v]concat=n=2:v=1:a=0[out]" -map "[out]" "{output}" -y'

    subprocess.call(cmd, shell=True)

    await msg.reply_video(FSInputFile(output), caption="🎉 মার্জ সম্পন্ন!")

    # clean
    os.remove(v1)
    os.remove(v2)
    os.remove(output)
    user_files[user_id] = []


async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

