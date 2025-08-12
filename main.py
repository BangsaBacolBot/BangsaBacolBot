from dotenv import load_dotenv
load_dotenv()

import asyncio
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from pyrogram.enums import ParseMode
from pyrogram.enums import ChatMemberStatus
import os
import json
from datetime import datetime

# ✅ CEK KEANGGOTAAN USER
async def is_member(client, user_id, chat_username):
    try:
        member = await client.get_chat_member(chat_username, user_id)
        print(f"[DEBUG] checking membership in {chat_username} for user_id {user_id} → status: {member.status} → is_member: {member.status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]}")
        return member.status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]
    except Exception as e:
        print(f"[DEBUG] Failed to get membership in {chat_username}: {e}")
        return False

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME")
GROUP_USERNAME = os.getenv("GROUP_USERNAME")
OWNER_ID = int(os.getenv("OWNER_ID"))

# Load stream map dari file JSON
def load_stream_map():
    with open("stream_links.json", "r") as f:
        return json.load(f)

def get_stream_data(code):
    data = load_stream_map().get(code)
    if isinstance(data, dict):
        return data.get("link"), data.get("thumbnail")
    elif isinstance(data, str):
        return data, "terbuka.jpg"
    return None, None

def log_click(app, user, code, url):
    log_line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] User {user.id} (@{user.username or 'unknown'}) klik: {code} → {url}\n"
    with open("access.log", "a", encoding="utf-8") as f:
        f.write(log_line)

# Inisialisasi bot
app = Client("bangsabacolbot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# /start command
@app.on_message(filters.command("start"))
async def start_command(client, message):
    if len(message.command) < 2:
        await message.reply_photo(
            photo="Img/usebot.jpg",
            caption=(
                "ℹ️ Untuk mencari Koleksi Kirim perintah seperti \n\n"
                "<code>/start nama_koleksi</code>\n\n"
                "Lihat daftar KODE koleksi di channel @Bangsabacol."
            ),
            parse_mode=ParseMode.HTML
        )
        return

    start_param = message.command[1]
    stream_link, thumbnail = get_stream_data(start_param)
    if not stream_link:
        await message.reply(
            f"❌ KODE <code>{start_param}</code> tidak ditemukan.\n\nSilakan cek ulang di channel @{CHANNEL_USERNAME} atau gunakan /list untuk melihat semua koleksi yang tersedia.",
            parse_mode=ParseMode.HTML
        )
        return

    buttons = [
        [InlineKeyboardButton("📢 JOIN CHANNEL", url=f"https://t.me/{CHANNEL_USERNAME}")],
        [InlineKeyboardButton("👥 JOIN GROUP", url=f"https://t.me/{GROUP_USERNAME}")],
        [InlineKeyboardButton("🔒 BUKA KOLEKSI", callback_data=f"verify_{start_param}")]
    ]

    await message.reply_photo(
        photo="Img/terkunci.jpg",
        caption=(
            "✨<b>AKSES KOLEKSI TERSEDIA!</b>✨\n\n"
            "SILAHKAN JOIN CHANNEL DAN GROUP DULU UNTUK MEMBUKA KOLEKSI!\n\n"
            "CARA NONTON VIDEO: https://t.me/BangsaBacol/26"
        ),
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode=ParseMode.HTML
    )

# Verifikasi koleksi
@app.on_callback_query()
async def handle_callback(client, callback_query):
    data = callback_query.data
    if data.startswith("verify_"):
        code = data.replace("verify_", "")
        user_id = callback_query.from_user.id

        # ✅ CEK apakah user sudah join CHANNEL dan GROUP
        is_channel_member = await is_member(client, user_id, CHANNEL_USERNAME)
        is_group_member = await is_member(client, user_id, GROUP_USERNAME)
    
        if not (is_channel_member and is_group_member):
            await callback_query.answer("❌ Kamu belum join channel & group!", show_alert=True)
            return

        # ✅ SUDAH JOIN → LANJUT
        stream_link, thumbnail = get_stream_data(code)
        if not stream_link:
            await callback_query.message.reply("❌ Link streaming tidak ditemukan.\n\nKODE KAMU SALAH, CEK ULANG KODE KAMU!")
            return

        log_click(client, callback_query.from_user, code, stream_link)
        button = InlineKeyboardMarkup([[InlineKeyboardButton("🔗 TONTON SEKARANG", url=stream_link)]])
        await callback_query.message.reply_photo(
            photo=f"Img/{thumbnail}" if thumbnail else "Img/terbuka.jpg",
            caption="✅ KLIK TOMBOL DIBAWAH UNTUK MENONTON!\n\nLAPOR JIKA LINK DAN BOT ERROR!",
            reply_markup=button
        )

# /list command → hanya untuk OWNER
@app.on_message(filters.command("list"))
async def list_streams(client, message):
    if message.from_user.id != OWNER_ID:
        return await message.reply("❌ Kamu tidak punya akses untuk melihat daftar koleksi. Cek Kode Koleksi di @BangsaBacol")

    stream_map = load_stream_map()
    if not stream_map:
        await message.reply("⚠️ Tidak ada koleksi yang tersedia saat ini.")
        return

    text = "<b>📺 Daftar Koleksi Streaming Tersedia:</b>\n\n"
    for code, info in stream_map.items():
        link = info["link"] if isinstance(info, dict) else info
        text += f"• <code>/start {code}</code> → <a href='{link}'>Link</a>\n"

    await message.reply(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

# /help command
@app.on_message(filters.command("help"))
async def help_command(client, message):
    text = """
🤖 <b>Daftar Perintah Bot</b>
/start <code>nama_koleksi</code> — Menonton koleksi (cek channel untuk kodenya)
/list — Lihat semua koleksi yang tersedia
/help — Menampilkan perintah
/about — Tentang bot ini
"""
    await message.reply(text, parse_mode=ParseMode.HTML)

@app.on_message(filters.command("about"))
async def about_command(client, message):
    await message.reply(
        "Aku <b>@BangsaBacolBot</b>, pelayan setia para Bangsa Bacol!\n\n"
        "Bot ini dibuat untuk memberikan akses cepat ke koleksi spesial tanpa ribet. Klik perintah, dan langsung nonton!\n\n"
        "📺 Cara nonton: https://t.me/BangsaBacol/26\n"
        "🔑 Join VIP: https://trakteer.id/BangsaBacol/showcase\n"
        "📢 Join Channel: @BangsaBacol\n"
        "💬 Join Group: @BangsaBacolGroup",
        parse_mode=ParseMode.HTML
    )

@app.on_message(filters.private & filters.text & ~filters.command(["start", "help", "about", "list"]))
async def unknown_message(client, message):
    await message.reply_photo(
        photo="Img/usebot1.jpg",
        caption=(
            "🤖<b>BUSET! AKU GAK NGERTI MAKSUDMU, MANUSIA...</b>\n\n"
            "💡 Coba ketik <code>/start nama_koleksi</code> untuk akses koleksi.\n"
            "Lihat daftar kode koleksi di channel @BangsaBacol.\n\n"
            "📺 Cara nonton: https://t.me/BangsaBacol/26\n"
            "🔑 Join VIP: https://trakteer.id/BangsaBacol/showcase"
        ),
        parse_mode=ParseMode.HTML
    )

# Sambut anggota baru
@app.on_message(filters.group & filters.new_chat_members)
async def greet_new_member(client, message):
    for user in message.new_chat_members:
        if user.is_bot:
            continue  # skip kalau yang join bot

        # Tombol join
        buttons = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 JOIN CHANNEL", url=f"https://t.me/{CHANNEL_USERNAME}")],
            [InlineKeyboardButton("👥 JOIN GROUP", url=f"https://t.me/{GROUP_USERNAME}")],
        ])

        welcome_text = (
            f"👋 Selamat datang {user.mention} di <b>{message.chat.title}</b>!\n\n"
            "📢 Pastikan kamu join channel & group untuk akses koleksi spesial.\n"
            "📺 Cara nonton: https://t.me/BangsaBacol/26\n\n"
            "Ketik: <code>/start nama_koleksi</code> untuk mulai."
        )

        # Kirim pesan sambutan
        sent_msg = await message.reply_text(
            welcome_text,
            parse_mode=ParseMode.HTML,
            reply_markup=buttons
        )

        # Tunggu 120 detik lalu hapus pesan
        await asyncio.sleep(120)
        try:
            await sent_msg.delete()
        except:
            pass  # kalau sudah dihapus manual atau tidak punya izin

# Jalankan bot
print("🚀 BOT AKTIF ✅ @BangsaBacolBot\nTekan CTRL+C untuk menghentikan.")
app.run()