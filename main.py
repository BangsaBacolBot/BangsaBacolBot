from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
import os
import json
from datetime import datetime

# Load env config
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME")
GROUP_USERNAME = os.getenv("GROUP_USERNAME")

# Fungsi load dari file JSON
def load_stream_map():
    with open("stream_links.json", "r") as f:
        return json.load(f)

# Fungsi logging akses user
def log_click(user, code, url):
    log_line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] User {user.id} (@{user.username or 'unknown'}) klik: {code} → {url}\n"
    with open("access.log", "a", encoding="utf-8") as f:
        f.write(log_line)

# Inisiasi Bot
app = Client("bangsabacolbot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Cek apakah user sudah join
async def is_member(client, chat, user_id):
    try:
        member = await client.get_chat_member(chat, user_id)
        return member.status in ["member", "administrator", "creator"]
    except:
        return False

# Handler command /start
@app.on_message(filters.command("start"))
async def start(client, message):
    user_id = message.from_user.id
    start_param = message.command[1] if len(message.command) > 1 else None
    stream_link = load_stream_map().get(start_param)

    if not stream_link:
        await message.reply("❌ Link streaming tidak ditemukan.")
        return

    in_channel = await is_member(client, f"@{CHANNEL_USERNAME}", user_id)
    in_group = await is_member(client, f"@{GROUP_USERNAME}", user_id)

    if in_channel and in_group:
        log_click(message.from_user, start_param, stream_link)
        button = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 TONTON SEKARANG", url=stream_link)]
        ])
        await message.reply("Klik tombol untuk menonton:", reply_markup=button)
    else:
        buttons = [
            [InlineKeyboardButton("📢 Join Channel", url=f"https://t.me/{CHANNEL_USERNAME}")],
            [InlineKeyboardButton("👥 Join Group", url=f"https://t.me/{GROUP_USERNAME}")],
            [InlineKeyboardButton("✅ Sudah Join Semua", callback_data=f"check_{start_param}")]
        ]
        await message.reply("Wajib join channel dan grup dulu sebelum nonton.", reply_markup=InlineKeyboardMarkup(buttons))

# Handler tombol "Sudah Join Semua"
@app.on_callback_query(filters.regex("check_(.*)"))
async def recheck_all(client, callback_query):
    user_id = callback_query.from_user.id
    param = callback_query.data.split("_", 1)[1]
    stream_link = load_stream_map().get(param)

    if not stream_link:
        await callback_query.answer("❌ Link tidak ditemukan.", show_alert=True)
        return

    in_channel = await is_member(client, f"@{CHANNEL_USERNAME}", user_id)
    in_group = await is_member(client, f"@{GROUP_USERNAME}", user_id)

    if in_channel and in_group:
        log_click(callback_query.from_user, param, stream_link)
        button = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 TONTON SEKARANG", url=stream_link)]
        ])
        await callback_query.message.edit("Klik tombol untuk menonton:", reply_markup=button)
        await callback_query.answer()
    else:
        await callback_query.answer("❌ Kamu belum join channel & grup.", show_alert=True)

# Jalankan bot
app.run()
