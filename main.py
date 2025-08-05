from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import os

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME")  # tanpa @
GROUP_USERNAME = os.getenv("GROUP_USERNAME")      # tanpa @
STREAM_LINK = os.getenv("STREAM_LINK")

app = Client("bangsabacolbot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

async def is_member(client, chat, user_id):
    try:
        member = await client.get_chat_member(chat, user_id)
        return member.status in ["member", "administrator", "creator"]
    except:
        return False

@app.on_message(filters.command("start"))
async def start(client, message):
    user_id = message.from_user.id
    in_channel = await is_member(client, f"@{CHANNEL_USERNAME}", user_id)
    in_group = await is_member(client, f"@{GROUP_USERNAME}", user_id)

    if in_channel and in_group:
        button = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 TONTON SEKARANG", url=STREAM_LINK)]
        ])
        await message.reply("Selamat menonton! Klik tombol di bawah:", reply_markup=button)
    else:
        buttons = [
            [InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{CHANNEL_USERNAME}")],
            [InlineKeyboardButton("👥 Join Group", url=f"https://t.me/{GROUP_USERNAME}")],
            [InlineKeyboardButton("✅ Sudah Join Semua", callback_data="check_all")]
        ]
        await message.reply("❌ Kamu belum join semua.

Wajib join channel & grup untuk lanjut.", reply_markup=InlineKeyboardMarkup(buttons))

@app.on_callback_query(filters.regex("check_all"))
async def recheck_all(client, callback_query):
    user_id = callback_query.from_user.id
    in_channel = await is_member(client, f"@{CHANNEL_USERNAME}", user_id)
    in_group = await is_member(client, f"@{GROUP_USERNAME}", user_id)

    if in_channel and in_group:
        button = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 TONTON SEKARANG", url=STREAM_LINK)]
        ])
        await callback_query.message.edit("Selamat menonton! Klik tombol di bawah:", reply_markup=button)
        await callback_query.answer()
    else:
        await callback_query.answer("❌ Kamu belum join channel & grup.", show_alert=True)

app.run()