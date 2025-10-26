import logging
import colorlog
import os
from pathlib import Path
import asyncio
import json
import re
from pyrogram.enums import ParseMode
from threading import Lock
from statistics import mean
from collections import defaultdict
from logging.handlers import RotatingFileHandler
from urllib.parse import urlparse
from datetime import datetime, timedelta

try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except ImportError:
    ZoneInfo = None

try:
    import psutil
except Exception:
    psutil = None

from dotenv import load_dotenv
from pyrogram.errors import UserNotParticipant
from pyrogram import Client, filters, idle
from pyrogram.types import Message
from pyrogram.enums import ChatMemberStatus, ParseMode
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, ChatPermissions
from pyrogram.errors import MessageNotModified
import aiohttp
from urllib.parse import quote_plus, unquote_plus
import time
import random
import urllib.request
import traceback
from pyrogram.types import InputMediaPhoto, InputMediaVideo, InputMediaDocument
from typing import Dict
import random as rnd
from pyrogram.types import (
    ForceReply,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
    Message
)

from functools import wraps
import time
from typing import Tuple

load_dotenv()

USER_DATA_FILE = Path("data/user_data.json")
VOTES_FILE = "votes.json"
user_states = {}

# Per-user asyncio locks (in-memory)
_USER_LOCKS: Dict[int, asyncio.Lock] = {}
_USER_LOCKS_LOCK = asyncio.Lock()

async def _get_user_lock(user_id: int) -> asyncio.Lock:
    """Return/create a per-user asyncio.Lock safely."""
    async with _USER_LOCKS_LOCK:
        lock = _USER_LOCKS.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            _USER_LOCKS[user_id] = lock
        return lock

def get_user_identity(user):
    """Ambil ID, username, dan fullname user Telegram."""
    user_id = user.id
    username = f"@{user.username}" if user.username else "NoUsername"
    fullname = " ".join(filter(None, [user.first_name, user.last_name])) or "NoName"
    return user_id, username, fullname

async def send_vip_log(client, text: str):
    """Kirim log aktivitas VIP ke channel khusus dengan auto delete 7 hari."""
    if LOG_CHANNEL_ID != 0:
        try:
            msg = await client.send_message(LOG_CHANNEL_ID, text)

            # auto delete setelah 7 hari
            async def auto_delete(m):
                try:
                    await asyncio.sleep(604800)  # 7 hari
                    await m.delete()
                except Exception as e:
                    print(f"[LOG DELETE ERROR] {e}")

            asyncio.create_task(auto_delete(msg))

        except Exception as e:
            print(f"[LOG ERROR] {e}")

# ======= Helpers untuk tier free & badge =======
def normalize_keys_required(value):
    if value is None:
        return 1
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        v = value.strip()
        if v == "?":
            return "?"
        try:
            return int(v)
        except ValueError:
            return 1
    return 1

def badge_rank(badge: str) -> int:
    """Return numeric rank for badges - adjust keywords to match badges in your app.
       Higher means stronger privilege. Tweak substring checks to fit your badges.
    """
    b = (badge or "").lower()
    if "starlord" in b or ("star" in b and "lord" in b):
        return 4
    if "stellar" in b:
        return 3
    if "shimmer" in b:
        return 2
    return 1  # stranger / default

def can_user_access_collection(user_badge: str, user_keys: int, keys_required):
    """Return True if user can access collection without spending keys."""
    # Shimmer tier (string '?') accessible for badge >= Shimmer
    if keys_required == -1:
        return badge_rank(user_badge) >= 2
    # Stellar tier (0) accessible for badge >= Stellar
    if keys_required == 0:
        return badge_rank(user_badge) >= 3
    # numeric price (pay with keys)
    try:
        needed = int(keys_required)
        return user_keys >= needed
    except Exception:
        # unknown format -> require not accessible
        return False

def keys_required_label(keys_required):
    if keys_required == "?":
        return "Gratis (Shimmer+)"
    if keys_required == 0:
        return "Gratis (Stellar+)"
    try:
        return f"{int(keys_required)} Key"
    except Exception:
        return str(keys_required)


# ---- Badge constants & helpers ----
BADGE_STRANGER = "Stranger 🔰"
BADGE_SHIMMER  = "Shimmer 🥉"  
BADGE_STELLAR  = "Stellar 🥈"
BADGE_STARLORD = "Starlord 🥇"

# --- Daftar emoji ---
EMOJIS = {
    "topup": ["💰","💵","💸","🪙","⚡","🔥","🏦","💳","📈","🤑"],
    "unlock": ["👑","💎","🐱‍🏍","🚪","✨","⭐","🔑","📂","🎉","🥂"],
    "xp": ["📈","💥","🎯","🏅","💡","🌟","⚡","🚀","🧩","🔝"],
    "badge": ["🏆","🎖","👑","⭐","🥇","🔥","🥂","💯","💪","🦾"],
    "freekey": ["🗝","🎉","⭐","🙌","🍀","🎁","📦","💫","✨"],
    "random": ["🎰","🎯","❓","🤞","🔮","🎲","🌀","♻️","🤩"],
    "listvip": ["📚","🗂️","📖","📒","🔎","🧾","📜","💦","🕵️"],
    "myvip": ["🔐","📦","💼","🔑","🪪","📁","💎","👝","🧳"],
    "vote": ["🗳️","📊","✅","📝","🤔","📢","💬","🙋","🔘"],
    "start": ["👋","🤖","🎬","🌟","⚡","🚀","🛸","🔔","📲"],
    "gift": ["🎊","💝","🥳","✨","🌈","🎀","🍬","💐","🎇"],
    "addvip": ["📦","💌","👑","🔥","🌟","🆕","💎","🎬","💠"],
    "claim": ["😍","✨","⭐","🎉","🙌","🎊","🪄","💖","🏅"],
    "claimbio": ["🤑","💣","⭐","🎉","🙌","🎊","🪄","🗓","🏅"],
}

# --- Template log per event (tanpa emoji di teks) ---
TEMPLATES = {
    "topup": [
        "💳 <i>Baru saja Top Up Key <b>{extra}</b></i>",
        "💳 <i>Berhasil Top Up nambah Saldo Key → <b>{extra}</b></i>",
        "💳 <i>Sukses Top Up dompet makin tebel <b>{extra}</b></i>",
        "💳 <i>Isi ulang Key biar makin gacor: <b>{extra}</b></i>",
        "💳 <i>Saldo Key naik level → <b>{extra}</b></i>",
    ],
    "unlock": [
        "🔓 <i>Telah resmi unlock koleksi premium <b>{extra}</b></i>",
        "🔓 <i>Membeli koleksi eksklusif → <b>{extra}</b></i>",
        "🔓 <i>Bacol dengan koleksi rahasia <b>{extra}</b></i>",
        "🔓 <i>Koleksi VIP kebuka! → <b>{extra}</b></i>",
        "🔓 <i>Berhasil dapetin akses koleksi spesial <b>{extra}</b></i>",
    ],
    "xp": [
        "⚡ <i>Dapet +1 XP gara - gara <b>{extra}</b></i>",
        "⚡ <i>Naikin XP lewat <b>{extra}</b></i>",
        "⚡ <i>Misi selesai dapet XP di <b>{extra}</b></i>",
        "⚡ <i>Tambah pengalaman dari <b>{extra}</b></i>",
        "⚡ <i>XP naik 1 level berkat <b>{extra}</b></i>",
    ],
    "badge": [
        "🤩 <i>Naik kasta nih! hadiah <b>{extra}</b></i>",
        "🤩 <i>Level up! dapet reward <b>{extra}</b></i>",
        "🤩 <i>Sekarang punya gelar baru, dapet reward <b>{extra}</b></i>",
        "🤩 <i>Keren! Badge naik. Hadiah <b>{extra}</b></i>",
        "🤩 <i>Rank up! Kini resmi naik level. Hadiah <b>{extra}</b></i>",
    ],
    "freekey": [
        "🙈 <i>Barusan Submit /freekey sukses</i>",
        "🙈 <i>Dapet Key gratis abis kirim koleksi di /freekey</i>",
        "🙈 <i>Setor Koleksi di /freekey</i>",
        "🙈 <i>Bonus Key cair lewat /freekey</i>",
        "🙈 <i>Free Key unlocked via /freekey</i>",
    ],
    "random": [
        "🎲 <i>Putar hoki di <b>/random</b></i>",
        "🎲 <i>Lagi gacha time di <b>/random</b></i>",
        "🎲 <i>Dapet koleksi gokil di <b>/random</b></i>",
        "🎲 <i>Spin nasib lewat /random</i>",
        "🎲 <i>Coba peruntungan di /random</i>",
    ],
    "myvip": [
        "📂 <i>Liat koleksi VIP sendiri (<b>{extra}</b>)</i>",
        "📂 <i>Punya koleksi nikmat <b>{extra}</b></i>",
        "📂 <i>Ngamanin koleksinya ada <b>{extra}</b></i>",
        "📂 <i>Cek ulang simpanan VIP <b>{extra}</b></i>",
        "📂 <i>Menikmati koleksi pribadi <b>{extra}</b></i>",
    ],
    "vote": [
        "💬 <i>Ngasih suara ke <b>{extra}</b></i>",
        "💬 <i>Ikutan vote hari ini: <b>{extra}</b></i>",
        "💬 <i>Vote locked → <b>{extra}</b></i>",
        "💬 <i>Pilihannya jatuh ke <b>{extra}</b></i>",
        "💬 <i>Kasih suara spesial buat <b>{extra}</b></i>",
    ],
    "start": [
        "🚀 <i>Menuju ke <b>{extra}</b></i>",
        "🚀 <i>OTW ke <b>{extra}</b></i>",
        "🚀 <i>Meluncur ke <b>{extra}</b></i>",
        "🚀 <i>Jalan ninja ke <b>{extra}</b></i>",
        "🚀 <i>Penerbangan langsung ke <b>{extra}</b></i>",
    ],
    "gift": [
        "🎁 <i>Hoki! dapet gift spesial dari Bangsa Bacol: <b>{extra}</b></i>",
        "🎁 <i>Dapet Key gratis dari Admin-Pusat → <b>{extra}</b></i>",
        "🎁 <i>Asik! dapet random gift: <b>{extra}</b></i>",
        "🎁 <i>Bonus tak terduga: <b>{extra}</b></i>",
        "🎁 <i>Rezeki nomplok berupa <b>{extra}</b></i>",
    ],
    "addvip": [
        "🆕 <i>Akhirnya upload koleksi VIP → <b>{extra}</b></i>",
        "🆕 <i>Merilis pack eksklusif: <b>{extra}</b></i>",
        "🆕 <i>Drop VIP pack baru → <b>{extra}</b></i>",
        "🆕 <i>Tambah koleksi spesial: <b>{extra}</b></i>",
        "🆕 <i>Koleksi fresh from oven: <b>{extra}</b></i>",
    ],
    "listvip": [
        "💦 <i>Masuk <b>{extra}</b> nyari bahan ritual!</i>",
        "💦 <i>Nge-scroll koleksi di <b>{extra}</b> siapa tau ada hidden gem</i>",
        "💦 <i>Lagi nyari koleksi <b>{extra}</b> buat Ritual Kenikmatan!</i>",
        "💦 <i>Eksplorasi koleksi penuh rasa di <b>{extra}</b></i>",
        "💦 <i>Berburu hidden treasure di <b>{extra}</b></i>",
    ],
    "claim": [
        "💖 <i>Ambil hadiah mingguan → <b>{extra}</b></i>",
        "💖 <i>Claim mingguan sukses: <b>{extra}</b></i>",
        "💖 <i>Hadiah mingguan cair → <b>{extra}</b></i>",
        "💖 <i>Reward mingguan masuk saldo → <b>{extra}</b></i>",
        "💖 <i>Mingguan tertebus: <b>{extra}</b></i>",
    ],
    "claimbio": [
        "💝 <i>Ambil hadiah harian → <b>{extra}</b></i>",
        "💝 <i>Claim harian sukses: <b>{extra}</b></i>",
        "💝 <i>Hadiah harian cair → <b>{extra}</b></i>",
        "💝 <i>Reward harian masuk saldo → <b>{extra}</b></i>",
        "💝 <i>Harian tertebus: <b>{extra}</b></i>",
    ],
    "zonk": [
        "😅 <i>Lagi nyetor koleksi kureng di /freekey!</i>",
        "😅 <i>Dapet Zonk! <b>{extra}</b> gara - gara koleksinya ditolak lewat /freekey</i>",
        "😅 <i>Submit koleksi di /freekey berakhir suram! ditolak admin! <b>{extra}</b></i>",
        "😅 <i>Koleksi gagal masuk, zonk parah! <b>{extra}</b></i>",
        "😅 <i>Freekey kali ini ga lolos, hasilnya zonk → <b>{extra}</b></i>",
    ],
}

def resolve_thumb(raw_thumb: str | None) -> str | None:
    if not raw_thumb:
        return None
    raw_thumb = str(raw_thumb).strip()

    if raw_thumb.startswith(("http://", "https://")):
        return raw_thumb
    if raw_thumb.startswith(("AgAC", "CAAC")) or len(raw_thumb) > 50:
        return raw_thumb
    thumb_path = get_thumb_path(raw_thumb)
    if thumb_path and os.path.exists(thumb_path):
        return thumb_path
    return None

def resolve_badge(user_id: int) -> str:
    data = load_user_data()
    return data.get(str(user_id), {}).get("badge", "Stranger 🔰")

# --- Fungsi log publik ---
async def send_public_log(
    client,
    event: str,
    badge: str = None,
    extra: str = "",
    photo: str = None,
    thumb: str = None,
    keys_required: int | str = None
):
    if PUBLIC_LOG_CHANNEL_ID == 0:
        logger.debug("[PUBLIC_LOG] dilewatkan (channel belum diset)")
        logger.info(f"[FAKE_LOG] Kirim log palsu UNLOCK: {badge} - {extra}")
        return
    
    # setelah unlock, langsung kasih XP log
    if event == "unlock":
        xp_extra = f"unlock_{extra}"
        await send_public_log(
                client,
        event="xp",
            badge=badge,
            extra=xp_extra
        )
        logger.info(f"[FAKE_LOG] XP dari unlock: {badge} - {xp_extra}")

    badge_text = badge or BADGE_STRANGER
    template = ""
    icon = ""

    # === Khusus event unlock ===
    if event == "unlock":
        if keys_required == "?":  # free shimmer
            template = "🥉 Koleksi <b>{extra}</b> ditambahkan gratis untuk Shimmer+!"
            icon = "🥉"
        elif keys_required == 0:  # free stellar
            template = "🥈 Koleksi <b>{extra}</b> ditambahkan gratis untuk Stellar+!"
            icon = "🥈"
        else:  # unlock berbayar
            icon = rnd.choice(EMOJIS.get("unlock", ["🔓"]))
            template = rnd.choice(TEMPLATES.get("unlock"))
    else:
        # Event lain → pakai default
        icon = rnd.choice(EMOJIS.get(event, ["🔥"]))
        template = rnd.choice(TEMPLATES.get(event, ["🆕 <i>Drop VIP pack baru → {extra}"]))

    # Hapus nested <b>
    caption = (
        f"<b>[ {badge_text} ]</b> 👉 <i><b>#{event.upper()}</b></i>\n"
        f"{template.format(extra=extra)} {icon}"
    )

    logger.debug(f"[PUBLIC_LOG] event='{event}', caption='{caption}'")

    try:
        image = resolve_thumb(thumb or photo)
        if image:
            await client.send_photo(
                PUBLIC_LOG_CHANNEL_ID,
                photo=image,
                caption=caption,
                parse_mode=ParseMode.HTML
            )
        else:
            await client.send_message(
                PUBLIC_LOG_CHANNEL_ID,
                caption,
                parse_mode=ParseMode.HTML
            )

        logger.debug("[PUBLIC_LOG] terkirim")
    except Exception as e:
        logger.error(f"[PUBLIC_LOG] gagal kirim: {e}")

# --- Helper pilih koleksi VIP random ---
def pick_random_vip(exclude_owned: bool = True):
    """Pilih koleksi VIP random. Jika exclude_owned=True, buang koleksi yang ada owner_id."""
    load_vip_map()
    if not VIP_MAP:
        return {"name": "unknown", "thumb": None, "keys": 1, "count": "?", "konten": "VIP Koleksi"}

    # filter kalau exclude_owned aktif
    candidates = {
        code: meta
        for code, meta in VIP_MAP.items()
        if not (exclude_owned and int(meta.get("owner_id", 0)) > 0)
    }

    if not candidates:  # fallback kalau semua ke-filter
        candidates = VIP_MAP

    key = random.choice(list(candidates.keys()))
    data = candidates[key] or {}
    return {
        "name": key,
        "thumb": data.get("thumbnail"),
        "keys": int(data.get("keys_required", 1)) if data.get("keys_required") is not None else 1,
        "count": str(data.get("media_count", "?")),
        "konten": data.get("konten", "VIP Koleksi")
    }


def pick_random_stream():
    """Pilih koleksi stream/free random. Pastikan stream map ter-load dulu."""
    load_stream_map()
    if not STREAM_MAP:
        return {"name": "free_unknown", "thumb": None, "konten": "Free Koleksi"}

    key = random.choice(list(STREAM_MAP.keys()))
    data = STREAM_MAP.get(key) or {}
    # STREAM_MAP entries bisa berupa dict atau string; tangani keduanya
    if isinstance(data, dict):
        link = data.get("link")
        thumb = data.get("thumbnail")
        konten = data.get("konten", "Free Koleksi")
    else:
        link = data
        thumb = None
        konten = "Free Koleksi"
    return {"name": key, "thumb": thumb, "konten": konten}

INFO_MESSAGES = [
    "⚠ <b>Semua aktivitas @BangsaBacolBot tercatat otomatis disini.</b>",
    "⚠ <b>Jangan kaget kalau gerakanmu ke-detect di log publik ini.</b>",
    "⚠ <b>Transparansi penuh: semua interaksi terekam di sini.</b>",
    "⚠ <b>Catatan otomatis, biar keliatan siapa yang aktif.</b>",
    "⚠ <b>Ini channel log aktivitas, mute jika terganggu!</b>",
    "⚠ <b>Ingat, ini log publik. Setiap langkahmu ada jejaknya.</b>"
]

# --- Pool badge dengan bobot ---
XP_COMMANDS = [
    "/start",
    "/random",
    "/listvip",
    "/profile",
    "/ping",
    "/lapor",
    "/search",
    "/free",
    "/joinvip",
    "/about",
    "/bot",
    "/panduan",
    "/freekey",
]

BADGE_POOL = [
    (BADGE_STRANGER, 0.6),
    (BADGE_SHIMMER,  0.3),
    (BADGE_STELLAR,  0.09),
    (BADGE_STARLORD, 0.01),
]

EVENT_POOL = [
    ("start",   0.35),  # dominan → banyak user baru
    ("xp",      0.30),  # dominan → banyak interaksi gratis
    ("listvip", 0.15),  # medium → user suka intip VIP
    ("unlock",  0.12),  # monetisasi jarang
    ("topup",   0.05),  # monetisasi lebih jarang
    ("claim",   0.03),  # sangat jarang
]


# --- Fungsi weighted random ---
def weighted_choice(pool):
    total = sum(item.get("weight", 1) for item in pool)
    r = random.uniform(0, total)
    upto = 0
    for item in pool:
        w = item.get("weight", 1)
        if upto + w >= r:
            return item
        upto += w
    return random.choice(pool)

def random_badge():
    return weighted_choice([{"badge": b, "weight": w} for b, w in BADGE_POOL])["badge"]

def random_event():
    pool = [{"event": e, "weight": w} for e, w in EVENT_POOL]
    return weighted_choice(pool)["event"]


# --- konfigurasi delay fake log ---
FAST_CHANCE = 0.3          # 30% kemungkinan cepat
FAST_RANGE = (60, 180)     # 1–3 menit
NORMAL_RANGE = (300, 900)  # 5–15 menit

# --- Fake log task ---
async def fake_log_task(client):
    await asyncio.sleep(3)
    logger.info("[FAKE_LOG] Loop start...")

    # 🔥 Kirim fake log pertama sekali pas bot nyala
    try:
        event = random_event()
        badge = random_badge()

        # pilih sumber koleksi sesuai event
        if event in ["unlock", "topup", "claim", "listvip"]:
            vip = pick_random_vip()       # koleksi VIP
        elif event == "start":
            vip = pick_random_stream()    # koleksi Free
        else:  # xp dan event lain
            vip = pick_random_stream()    # natural interaksi dari Free

        # generate extra sesuai event
        if event == "unlock":
            extra = f"{vip['name']}"
        elif event == "topup":
            keys = max(2, vip.get("keys", 2))
            extra = f"{keys} Key"
        elif event == "listvip":
            extra = "/listvip"
        elif event == "claim":
            extra = "2 Key"
        elif event == "xp":
            extra = random.choice(XP_COMMANDS)
        elif event == "start":
            extra = f"{vip['name']}"
        else:
            extra = vip['name']

        # thumbnail hanya untuk event tertentu
        thumb = None
        if event in ["unlock", "start"]:
            thumb = resolve_thumb(vip.get("thumb"))
            
        # kirim log pertama
        await send_public_log(
            client,
            event=event,
            badge=badge,
            extra=extra,
            thumb=thumb
        )
        logger.info(f"[FAKE_LOG] Kirim log pertama: {event} - {badge} - {extra}")

    except Exception as e:
        logger.error(f"[FAKE_LOG] error kirim log pertama: {e}")

    # --- Loop normal ---
    while True:
        try:
            # delay natural
            if random.random() < FAST_CHANCE:
                delay = random.randint(*FAST_RANGE)
            else:
                 delay = random.randint(*NORMAL_RANGE)
            logger.info(f"[FAKE_LOG] Tunggu {delay} detik...")
            await asyncio.sleep(delay)

            # skip kadang
            if random.random() < 0.1:
                logger.info("[FAKE_LOG] Skip kali ini (natural)")
                continue

            # 🎯 Chance untuk kirim info ringan
            if random.random() < 0.07:  # misal 7% dari semua loop
                info_text = random.choice(INFO_MESSAGES)
                await client.send_message(
                    PUBLIC_LOG_CHANNEL_ID,
                    info_text,
                    parse_mode=ParseMode.HTML
                )
                logger.info(f"[FAKE_LOG] Kirim info ringan: {info_text}")
                continue  # jangan kirim event di loop ini

            # --- ambil random event ---
            event = random_event()
            badge = random_badge()

            # pilih sumber koleksi sesuai event
            if event in ["unlock", "topup", "claim", "listvip"]:
                vip = pick_random_vip()
            elif event == "start":
                vip = pick_random_stream()
            else:
                vip = pick_random_stream()

            # generate extra sesuai event
            if event == "unlock":
                extra = f"{vip['name']}"
            elif event == "topup":
                keys = max(2, vip.get("keys", 2))
                extra = f"{keys} Key"
            elif event == "listvip":
                extra = "/listvip"
            elif event == "claim":
                extra = "2 Key"
            elif event == "xp":
                extra = random.choice(XP_COMMANDS)
            elif event == "start":
                extra = f"{vip['name']}"
            else:
                extra = vip['name']

            # thumbnail hanya untuk event tertentu
            thumb = None
            if event in ["unlock", "start"]:
                thumb = resolve_thumb(vip.get("thumb"))

            # kirim log
            await send_public_log(
                client,
                event=event,
                badge=badge,
                extra=extra,
                thumb=thumb
            )
            logger.info(f"[FAKE_LOG] Kirim log palsu: {event} - {badge} - {extra}")

        except Exception as e:
            logger.error(f"[FAKE_LOG] error: {e}")
            await asyncio.sleep(5)


def _parse_ids(env_key: str) -> list[int]:
    raw = os.getenv(env_key, "")
    parts = raw.replace(",", " ").split()
    return [int(x) for x in parts if x.strip().isdigit()]

# 2. Baru inisialisasi variabel
ADMIN_IDS = _parse_ids("ADMIN_IDS")
OWNER_IDS = _parse_ids("OWNER_ID")
ALLOWED_IDS = _parse_ids("ALLOWED_IDS")

# ================================
# Konfigurasi Lingkungan
# ================================

try:
    API_ID = int(os.getenv("API_ID"))
    API_HASH = os.getenv("API_HASH")
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    CHANNEL_USERNAME = os.getenv("CHANNEL_USERNAME")
    GROUP_USERNAME = os.getenv("GROUP_USERNAME")
    EXTRA_CHANNEL    = os.getenv("EXTRA_CHANNEL")
    PUBLIC_LOG_CHANNEL_ID = int(os.getenv("PUBLIC_LOG_CHANNEL_ID", "0"))
    CHANNEL_VIP = int(os.getenv("CHANNEL_VIP", "-1002709095559"))
    CHANNEL_CADANGAN = os.getenv("CHANNEL_CADANGAN")
except (TypeError, ValueError) as e:
    logger.error(f"Error loading environment variables: {e}")
    raise SystemExit(1)

app = Client(
    "BangsaBacolBot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

_MEMBERSHIP_CACHE: dict[int, dict] = {}  # { user_id: {"ok": bool, "reason": str, "expires_at": ts} }
_MEMBERSHIP_TTL = 30  # detik; sesuaikan

def _normalize_chat_identifier(raw: str) -> str:
    """Pastikan format chat untuk Pyrogram (pakai @user jika perlu)."""
    if not raw:
        return ""
    raw = str(raw).strip()
    if raw.startswith("-100") or raw.startswith("@"):
        return raw
    return "@" + raw

def _norm_chat(x: str) -> str:
    x = x.strip()
    return x if x.startswith("@") else f"@{x}"

async def verify_all_memberships(client, user_id: int) -> Tuple[bool, str]:
    now_ts = time.time()
    cache = _MEMBERSHIP_CACHE.get(user_id)
    if cache and cache.get("expires_at", 0) > now_ts:
        return cache["ok"], cache["reason"]

    missing = []
    cannot_verify = []

    checks = []
    if CHANNEL_USERNAME:
        checks.append(("Channel Utama", CHANNEL_USERNAME))
    if EXTRA_CHANNEL:
        checks.append(("Channel Backup", EXTRA_CHANNEL))
    if GROUP_USERNAME:
        checks.append(("Group", GROUP_USERNAME))

    for label, raw in checks:
        chat = _normalize_chat_identifier(raw)
        try:
            member = await client.get_chat_member(chat, user_id)
            status = getattr(member, "status", "")  # e.g. "member", "administrator", "creator", "left", "kicked"
            if status in ("left", "kicked", "banned", ""):
                missing.append((label, chat))
        except Exception as e:
            # Gagal verifikasi (mis. bot belum jadi member/admin, chat privat, rate limit, dll.)
            logger.warning(f"[verify_all_memberships] gagal cek {chat} untuk user {user_id}: {e}")
            cannot_verify.append((label, chat))

    if missing:
        lines = []
        for label, chat in missing:
            # buat link t.me yang aman (hilangkan @ jika ada)
            uname = chat.lstrip("@")
            lines.append(f"{label}: https://t.me/{uname}")
        reason = "Kamu belum bergabung ke:\n" + "\n".join(lines)
        ok = False
    elif cannot_verify:
        reason = (
            "Gagal memverifikasi keanggotaan. Pastikan bot sudah berada di grup/channel target "
            "(bot harus menjadi member; untuk channel privat biasanya bot perlu di-add sebagai admin)."
        )
        ok = False
    else:
        reason = "OK"
        ok = True

    _MEMBERSHIP_CACHE[user_id] = {"ok": ok, "reason": reason, "expires_at": now_ts + _MEMBERSHIP_TTL}
    return ok, reason

def require_membership(callback_data: str = "verify_membership"):
    def decorator(func):
        @wraps(func)
        async def wrapper(client, message, *args, **kwargs):
            # bypass untuk owner atau admin list
            user = getattr(message, "from_user", None)
            if not user:
                return await func(client, message, *args, **kwargs)

            user_id = user.id
            if OWNER_IDS and user_id == OWNER_IDS:
                return await func(client, message, *args, **kwargs)
            if user_id in ADMIN_IDS:
                return await func(client, message, *args, **kwargs)

            ok, reason = await verify_all_memberships(client, user_id)
            if not ok:
                buttons = [
                    [InlineKeyboardButton("📢 CHANNEL UTAMA", url=f"https://t.me/{CHANNEL_USERNAME.lstrip('@')}")],
                    [InlineKeyboardButton("🔁 CHANNEL BACKUP", url=f"https://t.me/{EXTRA_CHANNEL.lstrip('@')}")],
                    [InlineKeyboardButton("👥 JOIN GROUP", url=f"https://t.me/{GROUP_USERNAME.lstrip('@')}")],
                    [InlineKeyboardButton("🔓 CEK ULANG", callback_data=callback_data)],
                ]
                teks = (
                    "┏━━━━━━━━━━━━━━━\n"
                    "┃ 🔒 <b>Akses Ditolak</b>\n"
                    "┗━━━━━━━━━━━━━━━\n\n"
                    f"{reason}\n\n"
                    "👉 Klik tombol di bawah untuk join, lalu tekan <b>CEK ULANG</b>."
                )
                await message.reply_text(teks, reply_markup=InlineKeyboardMarkup(buttons), parse_mode=ParseMode.HTML)
                return
            return await func(client, message, *args, **kwargs)
        return wrapper
    return decorator

@app.on_callback_query(filters.regex(r"^verify_membership$"))
async def cb_verify_membership(client, cq: CallbackQuery):
    user_id = cq.from_user.id
    ok, reason = await verify_all_memberships(client, user_id)
    if not ok:
        await cq.answer(reason, show_alert=True)
        return

    # kalau sukses, beri tahu user; minta dia ulangi perintah (atau panggil fungsi spesifik jika known)
    await cq.message.edit_text(
        "┏━━━━━━━━━━━━━━━\n"
        "┃ ✅ <b>Akses Terverifikasi!</b>\n"
        "┗━━━━━━━━━━━━━━━\n\n"
        "✨ Silakan ulangi perintah yang ingin kamu gunakan (contoh: /listvip).",
        parse_mode=ParseMode.HTML
    )


# Imbalan (Key) saat user *naik ke* badge tersebut
BADGE_REWARDS = {
    BADGE_SHIMMER: 2,   # naik ke Shimmer
    BADGE_STELLAR: 4,   # naik ke Stellar 
    BADGE_STARLORD: 5, # naik ke Starlord 
}

def normalize_badge(name: str) -> str:
    if not name:
        return BADGE_STRANGER
    # toleransi untuk data lama yang tersimpan "Shimmer"
    fixed = name.replace("Shimmer", "Shimmer")
    # jaga-jaga trimming
    return fixed.strip()

def load_user_data():
    if USER_DATA_FILE.exists():
        with open(USER_DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def get_user_data(user_id: int) -> dict:
    data = load_user_data()
    user = data.get(str(user_id), {})
    if "key" not in user:
        user["key"] = 0
    if "badge" not in user:              # 🚩 tambahkan ini
        user["badge"] = BADGE_STRANGER
    data[str(user_id)] = user
    save_user_data(data)
    return user

_file_write_lock = Lock()

def save_user_data(data):
    USER_DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = USER_DATA_FILE.with_suffix(".tmp")
    with _file_write_lock:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, USER_DATA_FILE)

async def grant_xp_for_command(client, message, invoked_command: str, xp_increment: int = 1):
    user = message.from_user
    if not user:
        return

    user_id = user.id
    username = user.username or "-"
    mention = user.mention  # 🔑 Pyrogram punya .mention otomatis

    try:
        updated = update_user_xp(user_id, username, invoked_command, xp_increment=xp_increment)

        if updated.get("_xp_gained"):
            badge = updated.get("badge", BADGE_STRANGER)

            # 🔔 Notif personal ke user, sebut username
            await message.reply_text(
                f"⚡ @{username}, kamu dapat <i>+{xp_increment} XP</i> dari <code>/{invoked_command}</code>!",
                parse_mode=ParseMode.HTML
            )

            await send_public_log(client, "xp", badge=badge, extra=f"/{invoked_command}")
            await notify_badge_reward(client, message)

    except Exception as e:
        logger.error(f"[XP_ERROR] Gagal menambahkan XP untuk {user_id} ({username}): {e}")

BADGE_TIERS = [
    ("Starlord 🥇", 9999),
    ("Stellar 🥈", 200),
    ("Shimmer 🥉", 100),
    ("Stranger 🔰", 0),
]

def update_user_xp(user_id: int, username: str, invoked_command: str, xp_increment: int = 1) -> dict:
    data = load_user_data()
    user = data.setdefault(str(user_id), {
        "username": username or "-",
        "xp": 0,
        "badge": BADGE_STRANGER,
        "last_seen": None,
        "last_xp_dates": {}
    })

    user["username"] = username or user.get("username") or "-"
    now = datetime.now(JAKARTA_TZ)
    today = now.date().isoformat()

    last = user.setdefault("last_xp_dates", {})

    # --- Special: allow unlimited XP for unlock_* commands ---
    is_unlock_cmd = isinstance(invoked_command, str) and invoked_command.startswith("unlock_")

    # 🚩 Untuk command selain unlock_*, batasi 1x per hari (existing behaviour)
    if not is_unlock_cmd:
        if last.get(invoked_command) == today:
            user["last_seen"] = now.isoformat()
            save_user_data(data)
            user["_xp_gained"] = False
            return user

    prev_badge = normalize_badge(user.get("badge", BADGE_STRANGER))

    # ✅ Tambah XP baru (untuk unlock_*: selalu; untuk lainnya: hanya kalau belum diambil hari ini)
    user["xp"] = int(user.get("xp", 0)) + xp_increment
    user["last_seen"] = now.isoformat()
    # Tetap simpan last_xp_dates supaya history terjaga (untuk unlock_ kita bisa overwrite tiap hari, tapi tidak mencegah)
    last[invoked_command] = today

    # 🎖 Tentukan badge baru
    xp = user["xp"]
    if xp >= 9999:
        new_badge = BADGE_STARLORD
    elif xp >= 200:
        new_badge = BADGE_STELLAR
    elif xp >= 100:
        new_badge = BADGE_SHIMMER
    else:
        new_badge = BADGE_STRANGER
    user["badge"] = new_badge

    # 🏆 Cek naik badge → kasih reward Key
    if normalize_badge(new_badge) != prev_badge:
        reward = BADGE_REWARDS.get(normalize_badge(new_badge), 0)
        if reward > 0:
            user["key"] = int(user.get("key", 0)) + reward
            user["last_badge_reward"] = {
                "to_badge": new_badge,
                "reward": reward,
                "ts": now.isoformat()
            }
            user["pending_badge_reward"] = True
            try:
                logger.info(
                    f"[BADGE_REWARD] user={user_id} @{user.get('username')} "
                    f"{prev_badge}→{new_badge} +{reward} Key (saldo {user['key']})"
                )
            except Exception:
                pass

    # 💾 Simpan & tandai berhasil dapat XP baru
    save_user_data(data)
    user["_xp_gained"] = True
    return user

async def notify_badge_reward(client, message):
    """Kirim notif kalau user punya pending badge reward."""
    user_id = message.from_user.id
    data = load_user_data()
    user = data.get(str(user_id))

    if not user or not user.get("pending_badge_reward"):
        return

    reward_data = user.get("last_badge_reward")
    if not reward_data:
        return

    badge = reward_data["to_badge"]
    reward = reward_data["reward"]
    username = message.from_user.username or "user"

    teks = (
        f"🆙 Selamat @{username}!\n"
        f"Kamu naik ke badge <b>{badge}</b>\n"
        f"🎁 Hadiah: <b>+{reward} 🔑 Key</b>"
    )

    await message.reply_text(teks, parse_mode=ParseMode.HTML)

    # log ke admin
    if LOG_CHANNEL_ID:
        try:
            await client.send_message(
                LOG_CHANNEL_ID,
                f"🏆 @{username} naik ke {badge} (+{reward} Key)",
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass
        
    # 🚩 log publik anonim    
    try:
        await send_public_log(client, "badge", badge=badge, extra=f"+{reward} 🔑")
    except Exception as e:
        logger.error(f"Public log gagal: {e}")

    # hapus flag notif
    user["pending_badge_reward"] = False
    save_user_data(data)

@app.on_message(filters.command("profile") & filters.private)
async def profile_cmd(client, message):
    await notify_badge_reward(client, message)  # cek & kirim notif kalau ada
    ...

# Helper load/save
def load_votes():
    try:
        with open(VOTES_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_votes(data):
    with open(VOTES_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ================================
# JATAH /random 
# ================================
JAKARTA_TZ = ZoneInfo("Asia/Jakarta") if ZoneInfo else None
def _now_jkt():
    if JAKARTA_TZ:
        return datetime.now(JAKARTA_TZ)
    return datetime.utcnow() + timedelta(hours=7)

RANDOM_DAILY_LIMIT = 3
QUOTA_FILE = Path("data/random_quota.json")
_quota_lock = asyncio.Lock()

def _ensure_parent_dir(p: Path):
    p.parent.mkdir(parents=True, exist_ok=True)

def _load_quota() -> dict:
    if not QUOTA_FILE.exists():
        return {}
    try:
        with QUOTA_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_quota(data: dict) -> None:
    _ensure_parent_dir(QUOTA_FILE)
    with QUOTA_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def _today_key() -> str:
    return _now_jkt().date().isoformat()

def _seconds_until_midnight_jkt() -> int:
    now = _now_jkt()
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(0, int((tomorrow - now).total_seconds()))

def _format_eta(seconds: int) -> str:
    h = seconds // 3600
    m = (seconds % 3600) // 60
    if h > 0:
        return f"{h}j {m}m"
    return f"{m}m"

async def get_random_quota_status(user_id: int):
    async with _quota_lock:
        data = _load_quota()
        today = _today_key()
        if set(data.keys()) - {today}:
            data = {today: data.get(today, {})}
            _save_quota(data)
        used = int(data.get(today, {}).get(str(user_id), 0))
        limit = RANDOM_DAILY_LIMIT
        remaining = max(0, limit - used)
        return used, remaining, limit, _seconds_until_midnight_jkt()

async def consume_random_quota(user_id: int):
    async with _quota_lock:
        data = _load_quota()
        today = _today_key()
        if set(data.keys()) - {today}:
            data = {today: data.get(today, {})}
        daymap = data.setdefault(today, {})
        used = int(daymap.get(str(user_id), 0))
        if used >= RANDOM_DAILY_LIMIT:
            _save_quota(data)
            return False, 0, RANDOM_DAILY_LIMIT, _seconds_until_midnight_jkt()
        daymap[str(user_id)] = used + 1
        _save_quota(data)
        remaining_after = max(0, RANDOM_DAILY_LIMIT - (used + 1))
        return True, remaining_after, RANDOM_DAILY_LIMIT, _seconds_until_midnight_jkt()

# -----------CLAIM KEY (Fix JSON only)-------------
# Semua operasi key konsisten lewat file JSON
# non-locking version — dipanggil hanya ketika sudah memegang lock
def _deduct_user_key_no_lock(user_id: int, amount: int) -> bool:
    data = load_user_data()
    user = data.get(str(user_id), {})
    saldo = int(user.get("key", 0))
    if saldo < amount:
        return False
    saldo -= amount
    user["key"] = saldo
    data[str(user_id)] = user
    save_user_data(data)
    print(f"[DEBUG] _deduct_user_key_no_lock: {user_id} saldo = {saldo}")
    return True

def _add_user_key_no_lock(user_id: int, amount: int) -> None:
    data = load_user_data()
    user = data.get(str(user_id), {})
    saldo = int(user.get("key", 0)) + amount
    user["key"] = saldo
    data[str(user_id)] = user
    save_user_data(data)
    print(f"[DEBUG] _add_user_key_no_lock: {user_id} saldo = {saldo}")

# public async wrapper — bisa dipanggil standalone jika perlu
async def deduct_user_key(user_id: int, amount: int) -> bool:
    lock = await _get_user_lock(user_id)
    async with lock:
        return _deduct_user_key_no_lock(user_id, amount)

def add_user_key(user_id: int, amount: int):
    # tinggal re-use _add_user_key_no_lock supaya konsisten
    _add_user_key_no_lock(user_id, amount)
    return get_user_key(user_id)

def get_user_key(user_id: int) -> int:
    data = load_user_data()
    user = data.get(str(user_id), {})
    return int(user.get("key", 0))

def deduct_user_key(user_id: int, amount: int) -> bool:
    # tinggal re-use _deduct_user_key_no_lock supaya konsisten
    return _deduct_user_key_no_lock(user_id, amount)

def can_claim_weekly(user_id: int) -> tuple[bool, int]:
    """Cek apakah user bisa klaim. Return (boleh?, sisa_detik)."""
    data = load_user_data()
    user = data.get(str(user_id), {})
    last_claim = user.get("last_weekly_claim", 0)
    now = int(time.time())
    cooldown = 7 * 24 * 60 * 60  # 7 hari

    if now - last_claim >= cooldown:
        return True, 0
    else:
        remaining = cooldown - (now - last_claim)
        return False, remaining

def set_weekly_claim(user_id: int):
    data = load_user_data()
    user = data.get(str(user_id), {})
    user["last_weekly_claim"] = int(time.time())
    data[str(user_id)] = user
    save_user_data(data)

# ================================
# Utilitas
# ================================
USER_ACTIVITY_FILE = Path("data/user_activity.json")

def load_user_activity():
    if USER_ACTIVITY_FILE.exists():
        with open(USER_ACTIVITY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_user_activity(data):
    with open(USER_ACTIVITY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def log_user_activity(user_id, username):
    data = load_user_activity()
    user = str(user_id)
    if user not in data:
        data[user] = {"username": username, "count": 0}
    data[user]["count"] += 1
    data[user]["username"] = username  # update username jika berubah
    save_user_activity(data)


def _safe_parse_ts(ts: str):
    try:
        s = ts.strip()
        if s.endswith('Z'):
            s = s[:-1] + '+00:00'
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JAKARTA_TZ)
        return dt
    except Exception:
        return None

# ======================
# LOGGER SETUP
# ======================

LOG_DIR = Path("logs"); LOG_DIR.mkdir(exist_ok=True)
DATA_DIR = Path("data"); DATA_DIR.mkdir(exist_ok=True)

ACTIVITY_LOG = LOG_DIR / "bot_activity.log"
CLICKS_JSONL = LOG_DIR / "clicks.jsonl"
CLICKS_HUMAN = LOG_DIR / "clicks_human.log"
HEALTH_LOG = LOG_DIR / "health_check.log"
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "-1002850300588"))
CHANNEL_MEDIA = os.getenv("CHANNEL_MEDIA", "")
# auto-konversi ke int kalau diisi ID (mis. -100xxxxxxxxxx), kalau username biarkan string "@nama_channel"
if CHANNEL_MEDIA and (CHANNEL_MEDIA.startswith("-100") or CHANNEL_MEDIA.lstrip("-").isdigit()):
    CHANNEL_MEDIA = int(CHANNEL_MEDIA)


RETENTION_DAYS = 7
LOG_LEVEL = logging.INFO
MAX_LOG_SIZE = 10 * 1024 * 1024  # 10 MB
BACKUP_COUNT = 5

LOG_EMOJIS = {
    "DEBUG": "🐛",
    "INFO": "ℹ️",
    "WARNING": "⚠️",
    "ERROR": "❌",
    "CRITICAL": "🔥"
}

# 🎨 Formatter warna dengan variasi per komponen
console_formatter = colorlog.ColoredFormatter(
    "%(cyan)s%(asctime)s%(reset)s | "
    "%(log_color)s%(emoji)s [%(levelname)-8s]%(reset)s | "
    "%(bold_white)s%(name)s:%(lineno)d%(reset)s | "
    "%(white)s%(message)s%(reset)s",
    datefmt="%H:%M:%S",
    log_colors={
        "DEBUG": "cyan",
        "INFO": "green",
        "WARNING": "yellow",
        "ERROR": "red",
        "CRITICAL": "bold_red"
    }
)

class EmojiConsoleHandler(colorlog.StreamHandler):
    def emit(self, record):
        record.emoji = LOG_EMOJIS.get(record.levelname, "")
        super().emit(record)

console_handler = EmojiConsoleHandler()
console_handler.setFormatter(console_formatter)

file_handler = RotatingFileHandler(
    ACTIVITY_LOG, maxBytes=MAX_LOG_SIZE, backupCount=BACKUP_COUNT, encoding="utf-8"
)
file_formatter = logging.Formatter(
    "%(asctime)s | %(name)s | %(levelname)-8s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
file_handler.setFormatter(file_formatter)

logger = logging.getLogger("BangsaBacolBot")
logger.setLevel(LOG_LEVEL)
logger.addHandler(console_handler)
logger.addHandler(file_handler)

logging.getLogger("pyrogram").setLevel(logging.WARNING)

def cleanup_old_logs(directory: Path, retention_days: int):
    cutoff = datetime.now() - timedelta(days=retention_days)
    deleted_files = 0
    for file in directory.glob("*.log*"):
        try:
            mtime = datetime.fromtimestamp(file.stat().st_mtime)
            if mtime < cutoff:
                file.unlink()
                deleted_files += 1
        except Exception as e:
            logger.warning(f"Gagal menghapus log {file.name}: {e}")

    if deleted_files > 0:
        logger.info(f"🧹 {deleted_files} log lama dihapus (>{retention_days} hari).")
    else:
        logger.debug("Tidak ada log lama yang perlu dihapus.")

cleanup_old_logs(LOG_DIR, RETENTION_DAYS)
logger.info("Logger initialized! 🚀")

# --- Consolidated permission helpers (letakkan setelah env & user_data helper) ---
def is_admin(message) -> bool:
    """Return True kalau pengirim adalah owner atau termasuk ADMIN_IDS."""
    try:
        uid = message.from_user.id if getattr(message, "from_user", None) else 0
    except Exception:
        uid = 0
    return (uid == OWNER_IDS) or (uid in ADMIN_IDS)

def is_owner(ctx) -> bool:
    """Cek apakah user termasuk owner"""
    try:
        uid = ctx.from_user.id
    except Exception:
        return False
    return uid in OWNER_IDS

def is_starlord(user_id: int) -> bool:
    data = load_user_data()
    return normalize_badge(data.get(str(user_id), {}).get("badge", "")) == BADGE_STARLORD

def has_stellar_or_higher(user_id: int) -> bool:
    data = load_user_data()
    return normalize_badge(data.get(str(user_id), {}).get("badge", "")) in (BADGE_STELLAR, BADGE_STARLORD)

def can_access_collection(user_id: int, collection: dict) -> bool:
    """
    Cek akses user ke koleksi VIP:
    - Admin/Owner: full akses
    - Starlord: semua koleksi free tanpa key
    - User biasa: harus punya key sesuai keys_required
    """
    keys_required = collection.get("keys_required", 0)

    # Admin / Owner full akses
    if user_id in OWNER_IDS or user_id in ADMIN_IDS:
        return True

    # Starlord bisa semua free collection
    if is_starlord(user_id) and keys_required in [0, "?"]:
        return True

    # User normal → harus punya key
    return has_keys(user_id, keys_required)

# --- Retention Settings ---
try:
    RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "7"))  # default 7 hari
except ValueError:
    RETENTION_DAYS = 7

# ================================
# Config Loader: Badwords
# ================================
CONFIG_DIR = Path("config")
CONFIG_DIR.mkdir(exist_ok=True)

BADWORDS_CONFIG_URL = os.getenv("BADWORDS_CONFIG_URL")
BADWORDS_FILE = CONFIG_DIR / "badwords.json"

BAD_WORDS: set[str] = set()
BAD_WORDS_RE: re.Pattern = re.compile(r"(?!x)x")  # dummy regex
ALLOWED_LINK_DOMAINS: set[str] = {"t.me", "trakteer.id", "telegra.ph"}

def _build_badwords_regex(words: set[str]) -> re.Pattern:
    cleaned = [w.strip() for w in words if isinstance(w, str) and w.strip()]
    if not cleaned:
        return re.compile(r"(?!x)x")
    patt = r"\b(?:%s)\b" % "|".join(re.escape(w) for w in cleaned)
    try:
        return re.compile(patt, re.IGNORECASE)
    except re.error:
        return re.compile("|".join(re.escape(w) for w in cleaned), re.IGNORECASE)

def is_allowed_domain(url: str) -> bool:
    try:
        u = (url or "").strip()
        if not u:
            return False
        if "://" not in u:
            u = "https://" + u
        host = (urlparse(u).hostname or "").lower()
        if not host:
            return False
        return any(host == d or host.endswith("." + d) for d in ALLOWED_LINK_DOMAINS)
    except Exception:
        return False

def load_badwords_config():
    global BAD_WORDS, BAD_WORDS_RE, ALLOWED_LINK_DOMAINS
    remote_url = os.getenv("BADWORDS_CONFIG_URL", "").strip()
    data = None

    # 1) Remote
    if remote_url:
        try:
            logger.info(f"🔄 Fetching badwords config dari {remote_url}")
            with urllib.request.urlopen(remote_url, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            logger.warning(f"Gagal fetch remote config: {e}. Coba lokal...")

    # 2) Lokal
    if data is None and BADWORDS_FILE.exists():
        try:
            with open(BADWORDS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            logger.warning(f"Gagal baca {BADWORDS_FILE}: {e}")

    # 3) Default
    if data is None:
        logger.warning("Config badwords tidak ditemukan. Pakai fallback bawaan.")
        BAD_WORDS = {"tolol", "goblok", "bodoh"}
        ALLOWED_LINK_DOMAINS = {"t.me", "trakteer.id", "telegra.ph"}
        BAD_WORDS_RE = _build_badwords_regex(BAD_WORDS)
        return

    # Validasi
    words = data.get("badwords", []) or []
    domains = data.get("allowed_domains", []) or []

    BAD_WORDS = {str(w).strip() for w in words if str(w).strip()}
    ALLOWED_LINK_DOMAINS = {str(d).strip().lower() for d in domains if str(d).strip()}
    if not ALLOWED_LINK_DOMAINS:
        ALLOWED_LINK_DOMAINS = {"t.me", "trakteer.id", "telegra.ph"}

    BAD_WORDS_RE = _build_badwords_regex(BAD_WORDS)
    logger.info(f"✅ Badwords config loaded ({len(BAD_WORDS)} kata, {len(ALLOWED_LINK_DOMAINS)} domain).")

# ================================
# Anti-link & Bad Words Handler
# ================================
# --- Regex ---
URL_REGEX = re.compile(r"(https?://\S+|www\.\S+|t\.me/\S+)", re.IGNORECASE)
INVITE_REGEX = re.compile(r"(t\.me/joinchat/|t\.me/\+|telegram\.me/joinchat/)", re.IGNORECASE)

# --- Handler utama ---
@app.on_message(filters.text & filters.group, group=5)
async def moderation_guard(client, message: Message):
    text = (message.text or message.caption or "").strip()
    if not text:
        return

    # 1) Filter badwords
    if BAD_WORDS and BAD_WORDS_RE.search(text):
        try:
            await message.delete()
        except Exception:
            pass
        try:
            await message.reply("⚠️ Jaga bahasa ya, hindari kata-kata kasar.")
        except Exception:
            pass
        return

    # 2) Anti-link
    urls = [m.group(0) for m in URL_REGEX.finditer(text)]
    for u in urls:
        if not is_allowed_domain(u):
            try:
                await message.delete()
            except Exception:
                pass
            try:
                await message.reply("🔗 Link luar tidak diizinkan di sini.")
            except Exception:
                pass
            return

# ================================
# Loader: Interaction Config
# ================================
INTERACTION_CONFIG_URL = os.getenv("INTERACTION_CONFIG_URL")
INTERACTION_FILE = CONFIG_DIR / "interaction.json"
INTERACTION_MESSAGES: list[str] = []
INTERACTION_INTERVAL_MINUTES = 60

def load_interaction_config():
    global INTERACTION_MESSAGES, INTERACTION_INTERVAL_MINUTES
    data = {}
    try:
        if INTERACTION_CONFIG_URL:
            logger.info(f"🔄 Fetching interaction config dari {INTERACTION_CONFIG_URL}")
            with urllib.request.urlopen(INTERACTION_CONFIG_URL, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        elif INTERACTION_FILE.exists():
            with open(INTERACTION_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)

        msgs = data.get("interaction_messages", [])
        if msgs and isinstance(msgs, list):
            INTERACTION_MESSAGES = msgs

        INTERACTION_INTERVAL_MINUTES = int(
            data.get("interval_minutes", INTERACTION_INTERVAL_MINUTES)
        )

        logger.info(
            f"✅ Interaction config loaded "
            f"({len(INTERACTION_MESSAGES)} pesan, interval {INTERACTION_INTERVAL_MINUTES}m)."
        )
    except Exception as e:
        logger.error(f"Gagal load interaction config: {e}")


# ===============================
# --- LINK STREAM ---
# ===============================
STREAM_MAP_FILE = Path("stream_links.json")
STREAM_MAP: dict[str, dict] = {}
ITEMS_PER_PAGE = 9

# === VIP MAP ===
VIP_MAP_FILE = Path("vip_links.json")
VIP_MAP: dict[str, dict] = {}

VIP_COLLECTIONS_FILE = Path("vip_collections.json")
VIP_COLLECTIONS: dict[str, dict] = {}

# session sementara untuk admin mengumpulkan file_id via chat
FILECOLLECT_SESSIONS = {} 

BASE_DIR = Path(__file__).resolve().parent

# Fallback IMG_DIR
if Path("/mnt/e/BangsaBacolBot/Img").exists():
    IMG_DIR = Path("/mnt/e/BangsaBacolBot/Img")
else:
    IMG_DIR = BASE_DIR / "Img"
IMG_DIR.mkdir(exist_ok=True)

THUMB_DIR = IMG_DIR

def get_thumb_path(filename: str) -> str:
    if not filename:
        return str(IMG_DIR / "default.jpg")
    filename = str(filename).strip()
    # remote URL -> return as-is
    if filename.startswith("http://") or filename.startswith("https://"):
        return filename
    # absolute path -> return as-is
    if os.path.isabs(filename):
        return filename
    # otherwise treat as name inside IMG_DIR
    return str(IMG_DIR / filename)

async def safe_edit_markup(message, new_markup):
    try:
        await message.edit_reply_markup(reply_markup=new_markup)
    except Exception as e:
        if "MESSAGE_NOT_MODIFIED" in str(e):
            # abaikan kalau memang sama
            return
        raise

# ================================
# VIP STREAM MAP
# ================================

def load_vip_map() -> dict:
    global VIP_MAP
    try:
        if VIP_MAP_FILE.exists():
            with open(VIP_MAP_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
        else:
            raw = {}

        new_map = {}
        for code, obj in (raw or {}).items():
            if not isinstance(obj, dict) or "link" not in obj:
                continue
            # thumbnail: bisa URL atau filename; simpan apa adanya
            raw_thumb = obj.get("thumbnail")
            if isinstance(raw_thumb, str) and raw_thumb.strip():
                thumb_val = raw_thumb.strip()
            else:
                thumb_val = None

            # normalisasi minimal
            new_map[code] = {
                "link": obj["link"],
                "thumbnail": thumb_val,
                "keys_required": int(obj.get("keys_required", 1)) if obj.get("keys_required") is not None else 1,
                "media_count": str(obj.get("media_count", "?")),
                "konten": obj.get("konten", "Full Kolpri Premium"),
                "created_at": int(obj.get("created_at") or time.time()),
                "owner_id": int(obj.get("owner_id", 0))
            }

        VIP_MAP = new_map
        logger.info(f"[VIP] Loaded {len(VIP_MAP)} koleksi dari {VIP_MAP_FILE}")
        return VIP_MAP
    except Exception as e:
        logger.exception(f"[VIP] Load error: {e}")
        # jangan crash — biarkan map lama tetap jika ada
        return VIP_MAP

def save_vip_map() -> bool:
    global VIP_MAP
    try:
        normalized = {}
        for code, obj in (VIP_MAP or {}).items():
            normalized[code] = {
                "link": obj["link"],
                "thumbnail": obj.get("thumbnail"),
                "keys_required": obj.get("keys_required", 1),
                "media_count": str(obj.get("media_count", "?")),
                "konten": obj.get("konten", "Full Kolpri Premium"),
                "created_at": obj.get("created_at", int(time.time())),
                "owner_id": int(obj.get("owner_id", 0))
            }

        tmp = VIP_MAP_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(normalized, f, indent=2, ensure_ascii=False)
        # atomic replace
        os.replace(str(tmp), str(VIP_MAP_FILE))

        # update memory
        VIP_MAP = normalized
        logger.info(f"[VIP] Saved total {len(VIP_MAP)} koleksi ke {VIP_MAP_FILE}")
        return True

    except Exception as e:
        logger.exception("Gagal simpan vip_links.json:")
        return False

def has_vip_unlocked(user_id: int, code: str) -> bool:
    data = load_user_data()
    user = data.get(str(user_id), {})
    unlocked = user.get("vip_unlocked", [])
    return isinstance(unlocked, list) and (code in unlocked)

def mark_vip_unlocked(user_id: int, code: str) -> None:
    data = load_user_data()
    user = data.get(str(user_id), {})
    lst = user.get("vip_unlocked")
    if not isinstance(lst, list):
        lst = []
    if code not in lst:
        lst.append(code)
    user["vip_unlocked"] = lst
    data[str(user_id)] = user
    save_user_data(data)

def search_codes(query: str):
    load_vip_map()
    load_vip_collections()
    q = query.lower()
    results = []
    for code, meta in VIP_MAP.items():
        if q in code.lower() or q in str(meta.get("konten","")).lower():
            results.append(code)
    for code, meta in VIP_COLLECTIONS.items():
        if q in code.lower() or q in str(meta.get("konten","")).lower():
            if code not in results:
                results.append(code)
    return results

# ===============================
# COLLECTION MAP
# ================================
def load_vip_collections() -> dict:
    """Load vip_collections.json dan normalisasi struktur."""
    global VIP_COLLECTIONS
    try:
        if VIP_COLLECTIONS_FILE.exists():
            with open(VIP_COLLECTIONS_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
        else:
            raw = {}
        new_map = {}
        for code, obj in (raw or {}).items():
            if not isinstance(obj, dict):
                continue
            files = obj.get("files") or obj.get("file_ids") or []
            if not isinstance(files, list):
                continue

            # 🔑 keys_required: bisa angka, "0", "00", "?"
            raw_keys = obj.get("keys_required", 1)
            keys_required = normalize_keys_required(raw_keys)

            new_map[code] = {
                "files": files,
                "thumbnail": obj.get("thumbnail"),
                "keys_required": keys_required,
                "media_count": str(obj.get("media_count", len(files))),
                "konten": obj.get("konten", "Full Koleksi (file_id)"),
                "created_at": int(obj.get("created_at") or time.time()),
                "owner_id": int(obj.get("owner_id", 0))

            }
        VIP_COLLECTIONS = new_map
        logger.info(f"[VIP_COLLECTIONS] Loaded {len(VIP_COLLECTIONS)} file-based koleksi from {VIP_COLLECTIONS_FILE}")
    except Exception as e:
        logger.exception(f"[VIP_COLLECTIONS] Load error: {e}")
    return VIP_COLLECTIONS

def save_vip_collections() -> bool:
    global VIP_COLLECTIONS
    try:
        tmp = VIP_COLLECTIONS_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(VIP_COLLECTIONS, f, indent=2, ensure_ascii=False)
        os.replace(str(tmp), str(VIP_COLLECTIONS_FILE))
        logger.info(f"[VIP_COLLECTIONS] Saved {len(VIP_COLLECTIONS)} koleksi ke {VIP_COLLECTIONS_FILE}")
        return True
    except Exception as e:
        logger.exception("Gagal simpan vip_collections.json:")
        return False

# ===============================
# STREAM MAP
# ================================
def load_stream_map():
    global STREAM_MAP
    if not STREAM_MAP_FILE.exists():
        logger.warning(f"Berkas '{STREAM_MAP_FILE}' tidak ditemukan. Memulai dengan map kosong.")
        STREAM_MAP = {}
        return STREAM_MAP
    try:
        with open(STREAM_MAP_FILE, "r", encoding="utf-8") as f:
            STREAM_MAP = json.load(f)
    except Exception as e:
        logger.error(f"Gagal membaca {STREAM_MAP_FILE}: {e}. Memulai map kosong.")
        STREAM_MAP = {}
    return STREAM_MAP

def save_stream_map():
    with open(STREAM_MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(STREAM_MAP, f, indent=4, ensure_ascii=False)
    logger.info("Stream map disimpan.")

def get_stream_data(code: str):
    data = STREAM_MAP.get(code)
    if isinstance(data, dict):
        return data.get("link"), data.get("thumbnail")
    elif isinstance(data, str):
        return data, None
    return None, None

# --- Click Logging ---

def append_click_log(user_id, username, code, link):
    ts_human = datetime.now(JAKARTA_TZ).strftime("%Y-%m-%d %H:%M:%S")
    uname = f"@{username}" if username else "(unknown)"
    line = f"[{ts_human}] User {user_id} ({uname}) klik: {code} → {link}\n"

    event = {
        "ts": datetime.now(JAKARTA_TZ).isoformat(),
        "user_id": user_id,
        "username": username or None,
        "code": code,
        "link": link,
    }
    try:
        with open(CLICKS_JSONL, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.error(f"Gagal menulis clicks.jsonl: {e}")
    try:
        with open(CLICKS_HUMAN, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        logger.error(f"Gagal menulis clicks_human.log: {e}")

def prune_clicks_log(retention_days: int = RETENTION_DAYS):
    """Simpan hanya event dalam N hari terakhir (atomic replace)."""
    if not CLICKS_JSONL.exists():
        return
    cutoff = datetime.now(JAKARTA_TZ) - timedelta(days=RETENTION_DAYS)
    tmp_path = CLICKS_JSONL.with_suffix(".jsonl.tmp")
    with open(CLICKS_JSONL, "r", encoding="utf-8") as src, open(tmp_path, "w", encoding="utf-8") as dst:
        for line in src:
            try:
                ev = json.loads(line)
                ts = _safe_parse_ts(ev.get("ts", ""))
                if ts and ts >= cutoff:
                    dst.write(json.dumps(ev, ensure_ascii=False) + "\n")
            except Exception:
                continue
    os.replace(tmp_path, CLICKS_JSONL)

def prune_clicks_human(retention_days: int = RETENTION_DAYS):
    if not CLICKS_HUMAN.exists():
        return
    cutoff = datetime.now(JAKARTA_TZ) - timedelta(days=RETENTION_DAYS)
    out = []
    with open(CLICKS_HUMAN, "r", encoding="utf-8") as f:
        for ln in f:
            # format: "[YYYY-mm-dd HH:MM:SS] ...\n"
            try:
                ts = ln[1:20]
                dt = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=JAKARTA_TZ)
                if dt >= cutoff:
                    out.append(ln)
            except Exception:
                out.append(ln)
    with open(CLICKS_HUMAN, "w", encoding="utf-8") as f:
        f.writelines(out)

def parse_clicks_log_json(days_back: int = 7):
    """Ringkas logs/clicks.jsonl untuk N hari terakhir."""
    base = {
        "total_clicks": 0, "unique_users": 0, "by_day": {}, "by_code": {},
        "status": "success", "message": "", "debug": {}
    }
    if not CLICKS_JSONL.exists():
        r = base.copy(); r.update({"status": "no_log_file", "message": "File log belum ada."})
        return r

    cutoff = datetime.now(JAKARTA_TZ) - timedelta(days=RETENTION_DAYS)
    total, users, by_day, by_code = 0, set(), defaultdict(int), defaultdict(int)
    processed, errors = 0, 0

    try:
        with open(CLICKS_JSONL, "r", encoding="utf-8") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                try:
                    row = json.loads(s)
                except json.JSONDecodeError:
                    errors += 1; continue
                dt = _safe_parse_ts(row.get("ts", ""))
                if not dt:
                    errors += 1; continue
                if dt >= cutoff:
                    total += 1
                    uid = row.get("user_id"); 
                    if uid is not None: users.add(uid)
                    code = row.get("code") or row.get("link_key") or row.get("video_key") or "unknown"
                    by_code[code] += 1
                    by_day[dt.strftime("%Y-%m-%d")] += 1
                processed += 1

        status = "success" if total > 0 else "no_recent_clicks"
        out = base.copy()
        out.update({
            "status": status,
            "total_clicks": total,
            "unique_users": len(users),
            "by_day": dict(by_day),
            "by_code": dict(by_code),
            "message": "" if total > 0 else f"Tidak ada klik dalam {days_back} hari.",
            "debug": {"processed_lines": processed, "error_lines": errors, "cutoff_iso": cutoff.isoformat()}
        })
        return out
    except Exception as e:
        logger.error(f"Error membaca clicks.jsonl: {e}")
        r = base.copy(); r.update({"status": "read_error", "message": f"Error: {e}"})
        return r

def paginate_codes(codes, page, per_page=ITEMS_PER_PAGE):
    total = len(codes)
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, pages))
    start = (page - 1) * per_page
    end = min(start + per_page, total)
    return codes[start:end], page, pages, total

async def is_member(client: Client, user_id: int, chat_username: str) -> bool:
    try:
        m = await client.get_chat_member(_norm_chat(chat_username), user_id)
        return m.status in [ChatMemberStatus.MEMBER, ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER]
    except UserNotParticipant:
        # Bukan error—user memang belum join
        return False
    except Exception as e:
        logger.warning(f"Gagal cek membership {user_id} di {chat_username}: {e}")
        return False

def _check_log_file_status():
    info = {"exists": CLICKS_JSONL.exists(), "size": 0, "lines": 0, "tail": []}
    if not info["exists"]:
        return info
    try:
        info["size"] = CLICKS_JSONL.stat().st_size
        with open(CLICKS_JSONL, "r", encoding="utf-8") as f:
            lines = f.readlines()
        info["lines"] = len(lines)
        info["tail"] = [ln.strip() for ln in lines[-3:]]
    except Exception as e:
        info["error"] = str(e)
    return info

def build_dashboard_text(period_days: int = 7, top_n: int = 5):
    stats = parse_clicks_log_json(days_back=period_days)
    if stats["status"] in ("no_log_file", "read_error", "no_recent_clicks"):
        head = f"📊 Dashboard — {period_days} hari terakhir\n"
        body = f"• Total klik: {stats.get('total_clicks', 0)}\n• Pengguna unik: {stats.get('unique_users', 0)}\n"
        note = stats.get("message", "Belum ada data.")
        return head + body + f"\nℹ️ {note}"
    items = sorted(stats.get("by_code", {}).items(), key=lambda x: x[1], reverse=True)[:top_n]
    lines = [
        f"📊 Dashboard — {period_days} hari terakhir",
        f"• Total klik: {stats['total_clicks']}",
        f"• Pengguna unik: {stats['unique_users']}",
        "",
    ]
    if items:
        lines.append(f"🏆 Top {len(items)} Kode:")
        for i, (code, count) in enumerate(items, 1):
            lines.append(f"{i}. {code} — {count} klik")
    else:
        lines.append("Tidak ada data kode untuk periode ini.")
    if stats.get("by_day"):
        lines.append("")
        lines.append("🗓️ Ringkasan Harian:")
        for d, c in sorted(stats["by_day"].items())[-7:]:
            lines.append(f"• {d}: {c}")
    return "\n".join(lines)

def build_dashboard_keyboard(current_period: int = 7):
    periods = [1, 7, 30]
    row = []
    for p in periods:
        label = f"{p}d" if p != current_period else f"• {p}d"
        row.append(InlineKeyboardButton(label, callback_data=f"dashboard:{p}"))
    return InlineKeyboardMarkup([row, [InlineKeyboardButton("🔄 Refresh", callback_data=f"dashboard:{current_period}")]])

# ================================
# Bot Initialization
# ================================

app = Client("bangsabacolbot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- Perintah Umum ---
@app.on_message(filters.command("start") & filters.private)
async def start_command(client, message):
    await grant_xp_for_command(client, message, "start")
    user_id = message.from_user.id
    username = message.from_user.username or "NoUsername"


    # === cek badge dari user_data ===
    data = load_user_data()
    user = data.get(str(user_id), {"badge": "Stranger 🔰"})

    if len(message.command) > 1:
        param = message.command[1].lower()

        # === START LAPOR ===
        if param == "lapor":
            await grant_xp_for_command(client, message, "lapor")
            if user_id not in waiting_lapor_users:
                waiting_lapor_users.add(user_id)

            await message.reply(
                "👋 Hai, silahkan melapor!\n"
                "✍️ Kirim **teks atau media** (Foto, Video, Voice, Dokumen).\n"
                "❌ Kalau berubah pikiran, ketik **/batal**.\n\n"
                "⚠ **Tips:**\n"
                "Tuliskan semua laporanmu dalam satu kali kirim supaya Admin Pusat bisa langsung membacanya dengan jelas.",
                parse_mode=ParseMode.MARKDOWN
            )

            await send_public_log(client, "start", badge=user.get("badge"), extra="lapor")
            return

        # === START PANDUAN ===
        elif param == "panduan":
            await cmd_panduan(client, message)
            await send_vip_log(
                client,
                f"📖 START with param=panduan\nUser: @{username} (ID: <code>{user_id}</code>)"
            )
            await send_public_log(client, "start", badge=user.get("badge"), extra="panduan")
            return

        # === START KOLEKSI ===
        else:
            start_param = param
            stream_link, stream_thumb = get_stream_data(start_param)

            if not stream_link:
                await message.reply(
                    f"❌ KODE <code>{start_param}</code> tidak ditemukan.\n\n"
                    f"Silakan periksa kembali kodenya di channel @{CHANNEL_USERNAME}.\n\n"
                    "👉 Bantuan dan Dukungan:\n"
                    f"💌 <a href='https://t.me/BangsaBacol_Bot?start=lapor'>Lapor ke Admin</a>\n"
                    f"📜 <a href='https://t.me/BangsaBacol/8'>Daftar Bantuan</a>",
                    parse_mode=ParseMode.HTML
                )
                return

            vip_map = VIP_MAP.get(start_param, {})
            thumb = None
            if stream_thumb:
                thumb = get_thumb_path(stream_thumb)
            elif vip_map and "thumbnail" in vip_map:
                thumb = get_thumb_path(vip_map["thumbnail"])

            # ✅ fallback kalau file thumb gak ada
            DEFAULT_IMG = "Img/terkunci.jpg"
            if not thumb or not os.path.exists(thumb):
                photo_path = DEFAULT_IMG
            else:
                photo_path = thumb

            buttons = [
                [InlineKeyboardButton("📢 CHANNEL UTAMA", url=f"https://t.me/{CHANNEL_USERNAME}")],
                [InlineKeyboardButton("🔁 CHANNEL BACKUP", url=f"https://t.me/{EXTRA_CHANNEL}")],
                [InlineKeyboardButton("👥 JOIN GROUP", url=f"https://t.me/{GROUP_USERNAME}")],
                [InlineKeyboardButton("🔒 BUKA KOLEKSI", callback_data=f"verify_{start_param}")],
            ]

            await message.reply_photo(
                photo=photo_path,
                caption=(
                    "┏━━━━━━━━━━━━━━━\n"
                    "┃ ✨ <b>Akses Koleksi Tersedia!</b> ✨\n"
                    "┗━━━━━━━━━━━━━━━\n"
                    "📷 <b>Full koleksi Foto dan Video-nya cek di /listvip ya!</b>\n\n"
                    "🔐 Pastikan kamu sudah join <b>Channel & Group</b> untuk membuka koleksi.\n\n"
                    "🎁 <b>Jangan lupa /claim hadiah kamu!</b>\n\n"
                    "👉 <b>Bantuan & Dukungan:</b>\n"
                    f"💌 <a href='https://t.me/BangsaBacol_Bot?start=lapor'>Lapor ke Admin</a> | "
                    f"📜 <a href='https://t.me/BangsaBacol/8'>Daftar Bantuan</a>"
                ),
                reply_markup=InlineKeyboardMarkup(buttons),
                parse_mode=ParseMode.HTML
            )

            await send_vip_log(
                client,
                f"▶️ START with param=kode\nUser: @{username} (ID: <code>{user_id}</code>)\nKode: <code>{start_param}</code>"
            )

            await send_public_log(
                client,
                "start",
                badge=user.get("badge"),
                extra=f"kode {start_param}",
                thumb=photo_path
            )
            return

    # === DEFAULT START TANPA PARAMETER ===
    teks = (
        f"👋 <b>Selamat Datang <u>{username}</u></b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🤖 Aku adalah <u>Bangsa Bacol Bot</u>, asisten utama komunitas <b>Bangsa Bacol. Tugas utamaku:</b>\n"
        "<pre>"
        "🔑 Membuka Akses Koleksi\n"
        "📂 Memberikan Daftar Koleksi\n"
        "🎁 Mengatur Sistem Key\n"
        "🏆 Memberikan XP & Badge\n"
        "🛡️ Mengelola Ekosistem Bangsa Bacol\n"
        "</pre>"  
        "⚡ <b>Silahkan mulai dari:</b>\n"
        "• /profile → Cek Profil Kamu\n"
        "• /listvip → Daftar Koleksi\n"
        "• /claim → Hadiah Gratis Mingguan\n"
        "• /freekey → Ambil Freekey\n"
        "• /panduan → Baca Panduan Lengkap\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🆘 <b>Bantuan & Dukungan</b>\n"
        "🔔 <a href='https://t.me/BangsaBacol/8'>Daftar Bantuan</a>\n"
        "💌 <a href='https://t.me/BangsaBacol_Bot?start=lapor'>Lapor ke Admin Pusat</a>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🔥 <i>Selamat menjalani ritual kenikmatan ya!</i>"
    )

    await message.reply(teks, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

    await send_vip_log(
        client,
        f"👋 FIRST START\nUser: @{username} (ID: <code>{user_id}</code>)"
    )
    await send_public_log(client, "start", badge=user.get("badge"), extra="Kenikmatan!")

# ================= CALLBACK HANDLER =================
@app.on_callback_query(filters.regex(r"^verify_(?!listvip$).+$"))
async def cb_verify_collection(client, cq: CallbackQuery):
    user_id = cq.from_user.id
    kode = cq.data.split("_", 1)[1]

    # cek join
    ok, reason = await verify_all_memberships(client, user_id)
    if not ok:
        await cq.answer(reason, show_alert=True)
        return

    # ambil data stream
    data = get_stream_data(kode)
    if not data or not isinstance(data, (list, tuple)) or len(data) != 2:
        logger.error(f"[verify_collection] Data stream tidak valid untuk kode={kode}: {data}")
        await cq.answer("⚠️ Koleksi tidak ditemukan atau belum siap.", show_alert=True)
        return

    stream_link, stream_thumb = data
    thumb_path = get_thumb_path(stream_thumb) if stream_thumb else None

    buttons = InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️ Tonton Sekarang", url=stream_link)],
        [InlineKeyboardButton("📜 Lihat Koleksi Lain", callback_data="verify_listvip")],
        [InlineKeyboardButton("❌ Tutup", callback_data="list_close")]
    ])

    try:
        if thumb_path and os.path.exists(thumb_path):
            await cq.message.edit_media(
                media=InputMediaPhoto(
                    media=thumb_path,
                    caption=f"📷 Koleksi {kode} Terbuka!\nKlik tombol di bawah untuk menonton.",
                    parse_mode=ParseMode.HTML,
                ),
                reply_markup=buttons
            )
        else:
            await cq.message.edit_caption(
                caption=f"📷 <b>Koleksi <i>{kode}</i> Terbuka!\nKlik tombol di bawah untuk menonton.</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=buttons
            )

        await cq.answer("✅ Koleksi terbuka!", show_alert=False)

        # === ✅ Berikan XP saat unlock koleksi ===
        try:
            await grant_xp_for_command(client, cq.message, f"unlock_{kode}", xp_increment=1)
        except Exception as e:
            logger.error(f"[XP_UNLOCK_ERROR] {e}")

    except Exception as e:
        logger.error(f"[verify_collection] Gagal edit pesan untuk kode={kode}: {e}")
        await cq.answer("⚠️ Gagal membuka koleksi. Silakan coba lagi.", show_alert=True)

# ================== HELP MENU (Owner Only) ==================
@app.on_message(filters.command("help") & filters.private)
async def help_menu(client, message):
    if not is_owner(message):
        await message.reply("❌ Perintah ini hanya untuk Owner.")
        return

    help_text = """
🤖 <b>DAFTAR PERINTAH BANGSA BACOL BOT</b>

━━━━━━━━━━━━━━━━━━━━━━
👥 <b>UNTUK SEMUA PENGGUNA</b>
━━━━━━━━━━━━━━━━━━━━━━
• <code>/start kode</code> → Buka koleksi dengan kode  
• <code>/random</code> → Koleksi acak (3x sehari)  
• <code>/listvip</code> → Lihat Koleksi VIP  
• <code>/profile</code> → Lihat profil (XP, Badge, Key)  
• <code>/ping</code> → Cek status bot  
• <code>/lapor</code> → Lapor ke Admin Pusat  
• <code>/search kata</code> → Cari koleksi  
• <code>/free</code> → Koleksi gratis  
• <code>/joinvip</code> → Info unlock VIP penuh  
• <code>/request</code> → Request koleksi  
• <code>/about</code> → Info tentang bot  
• <code>/bot</code> → Daftar bot resmi  
• <code>/panduan</code> → Panduan penggunaan  
• <code>/freekey</code> → Free Key

━━━━━━━━━━━━━━━━━━━━━━
🎁 <b>FITUR KEY & VIP</b>
━━━━━━━━━━━━━━━━━━━━━━
• <code>/qris</code> → Isi saldo Key via QRIS  
• <code>/claim</code> → Ambil Key gratis mingguan  
• <code>/listvip</code> → Daftar Koleksi VIP  
• <code>/myvip</code> → Lihat koleksi yang sudah kamu buka  
• Koleksi yang sudah terbuka → bisa diakses ulang  
• <code>/setvip</code> → Promote VIP  
• <code>/unsetvip</code> → Cabut VIP 

━━━━━━━━━━━━━━━━━━━━━━
👑 <b>KHUSUS OWNER/ADMIN</b>
━━━━━━━━━━━━━━━━━━━━━━
📦 <b>Koleksi (Link)</b>  
• <code>/addvip</code> Kode Link Key → Tambah Koleksi VIP (link)  
• <code>/delvip</code> Kode → Hapus Koleksi VIP (link)  
• <code>/add</code> Kode Link → Tambah Koleksi biasa  
• <code>/delete</code> Kode → Hapus Koleksi biasa  

🗂 <b>Koleksi (File-ID)</b>  
• <code>/collectvip</code> → Mulai sesi pengumpulan file  
   ↳ Kirim media satu per satu (foto/video/dokumen)  
   ↳ Bot akan otomatis menyimpan file_id  
   ↳ Setelah selesai, gunakan <code>/finish_collect kode keys media_count</code>  
• <code>/abort_collect</code> → Batalkan sesi aktif  
• <code>/delcollect kode</code> → Hapus koleksi file_id (jika kamu menambahkan command ini)

🛠 <b>Manajemen & Utilitas</b>  
• <code>/stats</code> → Statistik klik 7 hari  
• <code>/dashboard</code> → Dashboard interaktif  
• <code>/healthcheck</code> → Cek kesehatan bot  
• <code>/reload_badwords</code> → Refresh daftar badwords  
• <code>/reload_interaction</code> → Refresh pesan interaksi  
• <code>/reset_top</code> → Reset leaderboard XP  
• <code>/topup</code> → Tambah saldo Key user  
• <code>/resetkey ID</code> → Reset Key 
• <code>/key ID</code> → Cek saldo Key user  
• <code>/hasil_request</code> → Lihat hasil vote request  
• <code>/giftkey</code> → Kirim Key berdasarkan hasil vote  

━━━━━━━━━━━━━━━━━━━━━━
ℹ️ <b>CATATAN</b>  
• Beberapa command sensitif hanya bisa dipakai oleh Owner/Admin  
• Moderator hanya aktif di group  
• Semua user bisa pakai perintah umum & VIP  
• Koleksi file_id memungkinkan kirim ulang file langsung via bot (lebih aman daripada link)  
"""
    await message.reply_text(help_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

@app.on_message(filters.command("panduan"))
@require_membership(callback_data="verify_panduan")
async def cmd_panduan(client, message):
    await grant_xp_for_command(client, message, "panduan")
    user = message.from_user.first_name if message.from_user else "Pengguna"
    username = f"@{message.from_user.username}" if (message.from_user and message.from_user.username) else user

    teks = f"""
┏━ 💡 <b>PANDUAN BANGSA BACOL</b> 💡 ━┓

👋 Hallo {username}, berikut adalah penjelasan singkat yang dapat membantumu.

🔑 <b>PERINTAH UMUM</b>
<pre>
/start kode   : Buka koleksi
/random       : Pilih koleksi acak
/listvip      : Daftar Koleksi lengkap
/profile      : Lihat profil detail
/ping         : Cek status bot
/lapor        : Hubungi Admin Pusat
/search kata  : Cari koleksi
/free         : Koleksi gratis
/joinvip      : Unlock VIP penuh
/request      : Request koleksi
/about        : Tentang bot ini
/bot          : Daftar bot resmi Bangsa Bacol</pre>
🎁 <b>FITUR KEY & VIP</b>
<pre>
/qris     : Isi saldo Key via QRIS
/claim    : Ambil Key gratis mingguan
/freekey  : Free Key (upload koleksi)
/listvip  : Koleksi VIP (unlock Key)
Koleksi yang sudah terbuka bisa diakses ulang tanpa biaya.</pre>
🏆 <b>XP & BADGE</b>
<pre>
Cara dapat XP:
Mainin command & Unlock koleksi
🔰 Stranger   : Awal     → 0 Key
🥉 Shimmer    : ≥100 XP  → +2 Key
🥈 Stellar    : ≥200 XP  → +4 Key
🥇 Starlord   : MEMBER VIP</pre>
🆘 <b>BANTUAN DAN DUKUNGAN</b>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔔 <a href="https://t.me/BangsaBacol/8">Daftar Bantuan</a> | 💌 <a href="https://t.me/BangsaBacol_Bot?start=lapor">Lapor ke Admin</a>  

🔥 <b>Selamat menikmati koleksi & jangan lupa ritual kenikmatan</b>💦
"""
    await message.reply_text(teks, disable_web_page_preview=True, parse_mode=ParseMode.HTML)


# ===============================
#  COMMAND: /listvip (FINAL FIX)
# ===============================

def parse_need(value) -> int:
    """Normalize keys_required ke integer standar.
    -1 → Free Shimmer, 0 → Free Stellar, >0 → VIP berbayar.
    """
    if str(value).strip() in ("?", "shimmer", "-1"):
        return -1
    if str(value).strip() in ("0", "stellar"):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 1  # default fallback

# --- Helper Escape MarkdownV2 ---
def escape_md(text: str) -> str:
    if not isinstance(text, str):
        text = str(text)
    escape_chars = r"_*[]()~`>#+-=|{}.!"
    for ch in escape_chars:
        text = text.replace(ch, f"\\{ch}")
    return text

def get_vip_meta(code: str) -> dict | None:
    """
    Ambil metadata koleksi berdasarkan kode, baik dari VIP_COLLECTIONS (file-based)
    maupun VIP_MAP (link-based).
    """
    load_vip_collections()
    load_vip_map()

    if code in VIP_COLLECTIONS:
        meta = VIP_COLLECTIONS[code].copy()
        meta["source"] = "files"
        return meta

    if code in VIP_MAP:
        meta = VIP_MAP[code].copy()
        meta["source"] = "link"
        return meta

    return None

def get_sort_key(code: str, meta: dict, sort_by: str):
    if sort_by == "need":
        # prioritas: jumlah key, fallback 999 biar di akhir
        need = parse_need(meta.get("keys_required", 999))
        return need if isinstance(need, int) else 999
    
    elif sort_by == "newest":
        # ambil timestamp atau created_at, fallback ke 0
        return -int(meta.get("created_at", 0))  # minus biar paling baru di atas
    
    else:  # default: abjad
        return code.lower()

# --- Build Keyboard List VIP ---
def build_vip_list_keyboard(
    page: int = 0, 
    user_id: int = None, 
    sort_by: str = "code"   # default abjad
) -> InlineKeyboardMarkup:
    load_vip_map()
    load_vip_collections()

    # gabungkan kode unik
    codes = list(set(list(VIP_MAP.keys()) + list(VIP_COLLECTIONS.keys())))

    # sorting
    def sort_func(c):
        meta = VIP_COLLECTIONS.get(c, VIP_MAP.get(c, {}))
        return get_sort_key(c, meta, sort_by)

    codes = sorted(codes, key=sort_func)

    total_pages = max(1, (len(codes) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    start = page * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE
    rows = []

    for code in codes[start:end]:
        if code in VIP_COLLECTIONS:
            meta = VIP_COLLECTIONS.get(code, {})
            source = "files"
        else:
            meta = VIP_MAP.get(code, {})
            source = "link"

        raw_need = meta.get("keys_required", 1)
        need = parse_need(raw_need)

        if need == -1:
            label = f"🥉 {code} • 0 🔑"
        elif need == 0:
            label = f"🥈 {code} • 0 🔑"
        elif isinstance(need, int):
            if need <= 3:
                label = f"⭐ {code} • {need} 🔑"
            elif need <= 5:
                label = f"👑 {code} • {need} 🔑"
            else:
                label = f"🔥 {code} • {need} 🔑"
        else:
            label = f"❓ {code} • {need}"

        if user_id and has_vip_unlocked(user_id, code):
            label = f"✅ {label}"

        rows.append([InlineKeyboardButton(label, callback_data=f"vip_detail|{code}|{page}|{sort_by}")])

    # navigation
    nav = []
    if page >= 10:
        nav.append(InlineKeyboardButton("⏪", callback_data=f"listvip_page|{page-10}|{sort_by}"))
    elif page > 0:
        nav.append(InlineKeyboardButton("⏪", callback_data=f"listvip_page|0|{sort_by}"))

    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"listvip_page|{page-1}|{sort_by}"))

    nav.append(InlineKeyboardButton(f"📖 {page+1}", callback_data="noop"))

    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"listvip_page|{page+1}|{sort_by}"))

    if page + 10 < total_pages:
        nav.append(InlineKeyboardButton("⏩", callback_data=f"listvip_page|{page+10}|{sort_by}"))
    elif page < total_pages - 1:
        nav.append(InlineKeyboardButton("⏩", callback_data=f"listvip_page|{total_pages-1}|{sort_by}"))

    if nav:
        rows.append(nav)

    # tombol sort
    rows.append([
        InlineKeyboardButton("🔤 Abjad", callback_data=f"listvip_sort|code|{page}"),
        InlineKeyboardButton("🔑 Keys", callback_data=f"listvip_sort|need|{page}"),
        InlineKeyboardButton("🆕 Terbaru", callback_data=f"listvip_sort|newest|{page}")
    ])

    rows.append([InlineKeyboardButton("🔎 Cari Koleksi", callback_data="listvip_search")])
    rows.append([InlineKeyboardButton("❌ Tutup", callback_data="listvip_close")])

    return InlineKeyboardMarkup(rows)

# ===============================
#   COMMAND: /listvip
# ===============================
async def verify_all_memberships(client, user_id: int) -> tuple[bool, str]:
    in_channel      = await is_member(client, user_id, CHANNEL_USERNAME)
    in_group        = await is_member(client, user_id, GROUP_USERNAME)
    is_extra_member = await is_member(client, user_id, EXTRA_CHANNEL)

    if not in_channel:
        return False, "❌ TERCYDUK BELUM JOIN CHANNEL UTAMA!"
    if not is_extra_member:
        return False, "❌ TERCYDUK BELUM JOIN CHANNEL BACKUP!"
    if not in_group:
        return False, "❌ TERCYDUK BELUM JOIN GROUP!"

    return True, "✅ Semua syarat sudah terpenuhi!"

@app.on_message(filters.command("listvip") & filters.private)
async def listvip_command(client, message: Message):
    await grant_xp_for_command(client, message, "listvip")
    user_id = message.from_user.id
    username = message.from_user.username or "NoUsername"

    # ambil data user (kalau ada, untuk badge)
    user = USERS.get(str(user_id), {}) if "USERS" in globals() else {}
    badge = user.get("badge", "Stranger 🔰")

    # 🔒 cek membership dulu
    ok, reason = await verify_all_memberships(client, user_id)
    if not ok:
        buttons = [
            [InlineKeyboardButton("📢 CHANNEL UTAMA", url=f"https://t.me/{CHANNEL_USERNAME}")],
            [InlineKeyboardButton("🔁 CHANNEL BACKUP", url=f"https://t.me/{EXTRA_CHANNEL}")],
            [InlineKeyboardButton("👥 JOIN GROUP", url=f"https://t.me/{GROUP_USERNAME}")],
            [InlineKeyboardButton("🔓 CEK ULANG", callback_data="verify_listvip")],
        ]

        teks = (
            "┏━━━━━━━━━━━━━━━\n"
            "┃ 🔒 <b>Akses VIP Terkunci</b>\n"
            "┗━━━━━━━━━━━━━━━\n\n"
            f"{reason}\n\n"
            "👉 Klik tombol di bawah untuk join, lalu tekan <b>CEK ULANG</b>."
        )

        await message.reply(
            teks,
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode=ParseMode.HTML
        )
        return

    # ✅ sudah join → langsung tampilkan halaman VIP
    await show_vip_page(message, 0)

    # ✅ log admin detail
    await send_vip_log(
        client,
        (
            "📒 <b>LIST VIP OPENED</b>\n"
            f"👤 User : @{username} (ID: <code>{user_id}</code>)\n"
        )
    )

    # ✅ log publik anonim
    try:
        data = load_user_data()
        user_data = data.get(str(user_id), {"badge": "Stranger 🔰"})

        # meta & code harus didefinisikan sesuai konteks show_vip_page
        await send_public_log(
            client,
            "listvip",
            badge=user_data.get("badge"),
            extra="/listvip"
        )
    except Exception as e:
        logger.error(f"Public log listvip gagal: {e}")

# ===============================
#  CALLBACK: verify_listvip
# ===============================
@app.on_callback_query(filters.regex(r"^verify_listvip$"))
async def cb_verify_listvip(client, cq: CallbackQuery):
    user_id = cq.from_user.id

    # cek join
    ok, reason = await verify_all_memberships(client, user_id)
    if not ok:
        await cq.answer(reason, show_alert=True)
        return

    # splash (opsional)
    try:
        await cq.message.edit_text(
            "┏━━━━━━━━━━━━━━━\n"
            "┃ ✅ <b>Akses Terverifikasi!</b>\n"
            "┗━━━━━━━━━━━━━━━\n\n"
            "✨ Membuka <b>List VIP</b> sekarang...",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        logger.error(f"[verify_listvip] Gagal edit pesan: {e}")
        await cq.answer("⚠️ Gagal membuka List VIP.", show_alert=True)
        return

    # tampilkan halaman VIP
    await show_vip_page(cq.message, 0)

    # konfirmasi ringan
    await cq.answer("🎉 List VIP terbuka!", show_alert=False)

# ===============================
#   Helper: safe edit / reply
# ===============================

async def safe_edit(message_or_cq, teks, reply_markup=None, parse_mode=None):
    from pyrogram.errors import MessageNotModified, MessageIdInvalid

    if isinstance(message_or_cq, CallbackQuery):
        try:
            return await message_or_cq.message.edit_text(
                teks, reply_markup=reply_markup, parse_mode=parse_mode
            )
        except (MessageNotModified, MessageIdInvalid):
            return await message_or_cq.message.reply_text(
                teks, reply_markup=reply_markup, parse_mode=parse_mode
            )
        except Exception:
            return await message_or_cq.message.reply_text(
                teks, reply_markup=reply_markup, parse_mode=parse_mode
            )
    elif isinstance(message_or_cq, Message):
        return await message_or_cq.reply_text(
            teks, reply_markup=reply_markup, parse_mode=parse_mode
        )

# ===============================
#   Show VIP page
# ===============================
async def show_vip_page(message_or_cq, page: int = 0, sort_by: str = "code"):
    load_vip_map()
    if not VIP_MAP:
        return

    user_id = message_or_cq.from_user.id
    saldo = get_user_key(user_id)

    # Ambil data user
    data = load_user_data()
    info = data.get(str(user_id), {
        "username": "NoUsername",
        "xp": 0,
        "badge": "Stranger 🔰",
        "last_xp_dates": {}
    })
    username = info.get("username", "NoUsername")
    badge = info.get("badge", "Stranger 🔰")

    teks = (
        "┏━━━━━━━━━━━━━━━\n"
        "┃ 👑 <b>KOLEKSI VIP</b> 👑\n"
        "┗━━━━━━━━━━━━━━━\n"
        "<pre>"
        f"User       : @{username}\n"
        f"ID         : {user_id}\n"
        f"Badge      : {badge}\n"
        f"Saldo Key  : {saldo}\n"
        "</pre>"
        "📂 <b>Silahkan pilih koleksi yang tersedia</b>\n"
        "📦 <i>Koleksi setiap hari update/bertambah</i>\n"
        "♻ <i>Item yang sudah terbuka bisa diakses ulang kapan saja tanpa biaya</i>"
    )

    keyboard = build_vip_list_keyboard(page=page, user_id=user_id, sort_by=sort_by)

    new_msg = await safe_edit(
        message_or_cq, teks, reply_markup=keyboard, parse_mode=ParseMode.HTML
    )

    # simpan last_msg_id untuk navigasi
    if new_msg:
        if user_id not in FREEKEY_SESSIONS:
            FREEKEY_SESSIONS[user_id] = {}
        FREEKEY_SESSIONS[user_id]["last_msg_id"] = new_msg.id

# ===============================
#   CALLBACK HANDLERS
# ===============================
from pyrogram.errors import FloodWait

async def _send_vip_collection(
    client, 
    user_id: int, 
    code: str, 
    meta: dict, 
    page: int, 
    sort_by: str,       
    gratis: bool = False,
    silent_link: bool = False
):
    files = meta.get("files", [])
    link = meta.get("link")

    if not files and not link:
        await client.send_message(user_id, f"⚠️ Koleksi {code} kosong.")
        return

    # ==== CASE MESSAGE_ID (int) ====
    if files and all(isinstance(f, int) for f in files):
        try:
            msgs = await client.get_messages(CHANNEL_VIP, files)
        except Exception as e:
            return await client.send_message(user_id, f"⚠️ Gagal ambil koleksi: {e}")

        media_batch = []
        last_mid = None

        for m in msgs:
            if not m:
                continue
            try:
                if m.photo:
                    media_batch.append(InputMediaPhoto(m.photo.file_id))
                elif m.video:
                    media_batch.append(InputMediaVideo(m.video.file_id))
                elif m.document:
                    media_batch.append(InputMediaDocument(m.document.file_id))
                else:
                    logger.warning(f"[VIP_SEND] jenis file belum didukung: {m.id}")
            except Exception as e:
                logger.error(f"[VIP_SEND] gagal convert message_id={getattr(m, 'id', '?')}: {e}")

        for i in range(0, len(media_batch), 10):
            chunk = media_batch[i:i+10]
            try:
                if len(chunk) == 1:
                    media = chunk[0]
                    if isinstance(media, InputMediaPhoto):
                        sent = [await client.send_photo(
                            user_id, 
                            media.media,
                            protect_content=True   # 🔒 Lindungi konten
                        )]
                    elif isinstance(media, InputMediaVideo):
                        sent = [await client.send_video(
                            user_id, 
                            media.media,
                            protect_content=True
                        )]
                    elif isinstance(media, InputMediaDocument):
                        sent = [await client.send_document(
                            user_id, 
                            media.media,
                            protect_content=True
                        )]
                    else:
                        sent = []
                else:
                    try:
                        sent = await client.send_media_group(
                            user_id, 
                            chunk,
                            protect_content=True    # 🔒 Lindungi semua media dalam grup
                        )
                    except FloodWait as e:
                        logger.warning(f"[VIP_SEND] FloodWait {e.value}s, tunggu dulu...")
                        await asyncio.sleep(e.value)
                        sent = await client.send_media_group(
                            user_id, 
                            chunk,
                            protect_content=True
                        )

                if sent:
                    last_mid = getattr(sent[-1], "id", None) or sent[-1].message_id
            except FloodWait as e:
                logger.warning(f"[VIP_SEND] FloodWait {e.value}s di luar, tunggu...")
                await asyncio.sleep(e.value)
            except Exception as e:
                logger.error(f"[VIP_SEND] gagal kirim album: {e}")

            await asyncio.sleep(2.0)

        if last_mid:
            try:
                await client.edit_message_caption(
                    chat_id=user_id,
                    message_id=last_mid,
                    caption=f"✅ Koleksi <b>{code}</b> {'gratis' if gratis else 'dikirim'} "
                            f"(total {len(files)})",
                    parse_mode=ParseMode.HTML
                )
            except Exception as e:
                logger.warning(f"[VIP_SEND] gagal edit caption: {e}")
        return

    # ==== CASE LINK ==== 
    if link:
        text = f"✅ Koleksi <b>{code}</b> {'gratis' if gratis else 'sudah terbuka'}."
        origin_msg_id = meta.get("origin_msg_id")
        origin_chat_id = meta.get("origin_chat_id")

        try:
            if origin_msg_id and origin_chat_id:
                # ✅ edit pesan lama user
                await client.edit_message_text(
                    chat_id=origin_chat_id,
                    message_id=origin_msg_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("▶️ Tonton Sekarang", url=link)],
                        [InlineKeyboardButton("⬅️ Kembali", callback_data=f"listvip_sort|{sort_by}|{page}")],
                        [InlineKeyboardButton("❌ Tutup", callback_data="listvip_close")]
                    ])
                )
                return
        except Exception as e:
            logger.error(f"[VIP_SEND LINK] gagal edit: {e}")


# --- Navigasi Halaman ---
@app.on_callback_query(filters.regex(r"^listvip_sort\|(.+?)\|(\d+)$"))
async def cb_listvip_sort(client, cq):
    sort_by, page = cq.data.split("|")[1:]
    page = int(page)

    kb = build_vip_list_keyboard(page=page, user_id=cq.from_user.id, sort_by=sort_by)

    # aman dari error MESSAGE_NOT_MODIFIED
    await safe_edit_markup(cq.message, kb)

    # kasih notifikasi sekali aja, dengan label rapi
    label = "🔤 Abjad" if sort_by == "code" else "🔑 Keys" if sort_by == "need" else "🆕 Terbaru"
    await cq.answer(f"📑 Diurutkan berdasarkan: {label}")

@app.on_callback_query(filters.regex(r"^listvip_page\|(\d+)(?:\|(\w+))?$"))
async def cb_listvip_page(client, cq: CallbackQuery):
    parts = cq.data.split("|")
    page = int(parts[1])
    sort = parts[2] if len(parts) > 2 else "code"  # default abjad

    await show_vip_page(cq, page, sort)

# --- Detail VIP ---
@app.on_callback_query(filters.regex(r"^vip_detail\|(.+?)\|(\d+)\|(.+)$"))
async def cb_vip_detail(client, cq: CallbackQuery):
    user_id = cq.from_user.id
    username = cq.from_user.username or "NoUsername"

    try:
        _, code, page_str, sort_by = cq.data.split("|")
        page = int(page_str)
    except Exception:
        await cq.answer("❌ Data tidak valid.", show_alert=True)
        return

    from_search = (sort_by == "search")  # ✅ baru taruh di sini

    meta = get_vip_meta(code)
    if not meta:
        await cq.answer("⚠️ Item VIP tidak ditemukan.", show_alert=True)
        return

    # ✅ parse kebutuhan key
    need = parse_need(meta.get("keys_required", 1))
    saldo = get_user_key(user_id)

    # 🔖 buat label harga
    if need == -1:
        price_label = "Free Shimmer Plus"
    elif need == 0:
        price_label = "Free Stellar Plus"
    else:
        price_label = f"{need} Key"

    # 🚩 log admin
    await send_vip_log(
        client,
        (
            "👁 <b>VIP DETAIL OPENED</b>\n"
            f"👤 User   : @{username} (ID: <code>{user_id}</code>)\n"
            f"📦 Kode   : {code}\n"
            f"💰 Harga  : {price_label}\n"
            f"🔑 Saldo  : {saldo} Key\n"
        )
    )

    caption = (
        "┏━━━━━━━━━━━━━\n"
        "┃ ✨ **KOLEKSI EKSKLUSIF** ✨\n"
        "┗━━━━━━━━━━━━━\n"
        f"🔒 **Kode**   : ||**{escape_md(code)}**||\n"
        f"📷 **Media**  : ||**{escape_md(meta.get('media_count', '?'))}+ Media**||\n"
        f"💰 **Harga**  : ||**{escape_md(price_label)}**||\n"
        f"📦 **Konten** : **Full Kolpri Premium**\n"
        "━━━━━━━━━━━━━━━\n"
        f"🔑 Saldo Kamu : **{escape_md(saldo)}**\n"
        "⚠️ Pastikan saldo Key mencukupi!\n"
        "🔄 Isi saldo via `/qris` fast proses!\n"
        "━━━━━━━━━━━━━━━\n"
        "👉 Klik tombol **Konfirmasi** untuk lanjut"
    )

    # 🔙 tombol back → beda kalau dari search
    if from_search:
        back_btn = InlineKeyboardButton("⬅️ Kembali", callback_data=f"search_page|{code}|{page}")
    else:
        back_btn = InlineKeyboardButton("⬅️ Kembali", callback_data=f"listvip_page|{page}|{sort_by}")

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✅ Konfirmasi Buka ({price_label})", callback_data=f"vip_confirm|{code}|{need}|{page}|{sort_by}")],
        [back_btn]
    ])

    thumb = resolve_thumb(meta.get("thumbnail"))

    try:
        if thumb:
            new_msg = await cq.message.reply_photo(
                photo=thumb,
                caption=caption,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=kb
            )
            FREEKEY_SESSIONS.setdefault(user_id, {})["last_msg_id"] = new_msg.id
            try:
                await cq.message.delete()
            except:
                pass
        else:
            new_msg = await safe_edit(
                cq,
                caption,
                reply_markup=kb,
                parse_mode=ParseMode.MARKDOWN
            )
            if new_msg:
                FREEKEY_SESSIONS.setdefault(user_id, {})["last_msg_id"] = new_msg.id
    except Exception as e:
        logger.debug(f"[VIP_DETAIL] code={code!r} thumb={meta.get('thumbnail')!r}")
        logger.error(f"[VIP_DETAIL_ERROR] {e}")
        await cq.answer(f"Terjadi error: {e}", show_alert=True)


# --- Helper: tombol dinamis ---
def make_vip_buttons(link: str, page: int, sort_by: str) -> InlineKeyboardMarkup:
    buttons = []
    if link:
        buttons.append([InlineKeyboardButton("▶️ Tonton Sekarang", url=link)])
    buttons.append([InlineKeyboardButton("⬅️ Kembali", callback_data=f"listvip_sort|{sort_by}|{page}")])
    buttons.append([InlineKeyboardButton("❌ Tutup", callback_data="listvip_close")])
    return InlineKeyboardMarkup(buttons)


# --- Konfirmasi VIP ---
@app.on_callback_query(filters.regex(r"^vip_confirm\|(.+?)\|(.+?)\|(\d+)\|(.+)$"))
async def cb_vip_confirm(client, cq: CallbackQuery):
    try:
        _, code, need_s, page_s, sort_by = cq.data.split("|")
        need = parse_need(need_s)
        page = int(page_s)
    except Exception:
        await cq.answer("❌ Data tidak valid.", show_alert=True)
        return

    user_id = cq.from_user.id
    saldo = get_user_key(user_id)

    # --- Tentukan label harga ---
    if need == -1:  # Free Shimmer+
        if not has_shimmer_or_higher(user_id):
            await cq.answer("❌ Koleksi ini gratis hanya untuk Shimmer 🥉 ke atas!", show_alert=True)
            return
        price_label = "Free (Shimmer+)"
    elif need == 0:  # Free Stellar+
        if not has_stellar_or_higher(user_id):
            await cq.answer("❌ Koleksi ini gratis hanya untuk Stellar 🥈 ke atas!", show_alert=True)
            return
        price_label = "Free (Stellar+)"
    else:  # Normal
        if saldo < need:
            await cq.answer("⚠️ Saldo key kamu tidak cukup!", show_alert=True)
            return
        price_label = f"{need} Key"

    caption = (
        "┏━━━━━━━━━━━━━\n"
        "┃ ⚠️ <b>KONFIRMASI PEMBELIAN</b> ⚠️\n"
        "┗━━━━━━━━━━━━━\n"
        f"📦 Koleksi : <b>{code}</b>\n"
        f"💰 Harga   : <b>{price_label}</b>\n"
        "━━━━━━━━━━━━━━━\n"
        "🔥 Yakin mau buka koleksi ini? 🚀"
    )

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔓 Lanjut Buka", callback_data=f"vip_unlock|{code}|{need_s}|{page}|{sort_by}")],
        [InlineKeyboardButton("⬅️ Batalkan", callback_data=f"listvip_sort|{sort_by}|{page}")]
    ])

    try:
        if cq.message.photo:
            await cq.message.edit_caption(caption, parse_mode=ParseMode.HTML, reply_markup=kb)
        else:
            await cq.message.edit_text(caption, parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception as e:
        logger.error(f"[VIP_CONFIRM_ERROR] {e}")

    await cq.answer()

# --- Unlock VIP ---
@app.on_callback_query(filters.regex(r"^vip_unlock\|(.+?)\|(.+?)\|(\d+)\|(.+)$"))
async def cb_vip_unlock(client, cq: CallbackQuery):
    user_id = cq.from_user.id
    username = cq.from_user.username or "NoUsername"
    logger.info(f"[VIP_UNLOCK] Callback data: {cq.data}")

    # --- Parse data ---
    try:
        _, code, need_s, page_s, sort_by = cq.data.split("|")
        need = parse_need(need_s)
        page = int(page_s)
    except Exception as e:
        await cq.answer(f"Data tidak valid: {e}", show_alert=True)
        return

    # --- Ambil metadata VIP ---
    meta = get_vip_meta(code)
    if not meta:
        await cq.answer("⚠️ Item VIP tidak ditemukan.", show_alert=True)
        return
    
    need = parse_need(meta.get("keys_required", 1))
    link = meta.get("link")

    # --- ADMIN / OWNER / STARLORD BYPASS ---
    if is_owner(cq) or is_admin(cq) or is_starlord(user_id):
        try:
            meta["origin_msg_id"] = cq.message.id
            await _send_vip_collection(client, user_id, code, meta, page, sort_by, gratis=False, silent_link=False)
        except Exception as e:
            await cq.answer(f"❌ Gagal kirim koleksi: {e}", show_alert=True)
            return

        reply_markup = make_vip_buttons(link, page, sort_by)
        try:
            await cq.message.edit_caption(
                caption=f"✅ <b>{code}</b> terbuka gratis (admin/owner/starlord).",
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup
            )
        except Exception:
            await cq.message.edit_text(
                f"✅ <b>{code}</b> terbuka gratis (admin/owner/starlord).",
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup
            )
        await cq.answer("✅ Koleksi terbuka gratis (admin/owner/starlord).")

        # 🔹 log bypass
        await send_vip_log(
            client,
            (
                "👑 <b>BYPASS UNLOCK</b>\n"
                f"👤 User   : @{username} (ID: <code>{user_id}</code>)\n"
                f"📦 Kode   : {code}\n"
                "🔑 Dipakai: 0 Key\n"
                f"💰 Sisa   : {get_user_key(user_id)} Key\n"
            )
        )
        return

    # --- USER BIASA ---
    lock = await _get_user_lock(user_id)
    if lock.locked():
        await cq.answer("⏳ Proses unlock sedang berjalan... tunggu sebentar.", show_alert=True)
        return

    async with lock:
        try:
            # --- Sudah pernah unlock ---
            if has_vip_unlocked(user_id, code):
                try:
                    meta["origin_msg_id"] = cq.message.id
                    await _send_vip_collection(client, user_id, code, meta, page, sort_by, gratis=False, silent_link=False)
                except Exception as e:
                    await cq.answer(f"❌ Gagal kirim ulang koleksi: {e}", show_alert=True)
                    return

                reply_markup = make_vip_buttons(link, page, sort_by)
                try:
                    await cq.message.edit_caption(
                        caption=f"✅ <b>{code}</b> sudah jadi milikmu.",
                        parse_mode=ParseMode.HTML,
                        reply_markup=reply_markup
                    )
                except Exception:
                    await cq.message.edit_text(
                        f"✅ <b>{code}</b> sudah jadi milikmu.",
                        parse_mode=ParseMode.HTML,
                        reply_markup=reply_markup
                    )
                await cq.answer("📦 Koleksi dikirim ulang / sudah kamu miliki.")

                # 🔹 log resend
                await send_vip_log(
                    client,
                    (
                        "♻️ <b>VIP RESEND</b>\n"
                        f"👤 User   : @{username} (ID: <code>{user_id}</code>)\n"
                        f"📦 Kode   : {code}\n"
                        "🔑 Dipakai: 0 Key (sudah pernah unlock)\n"
                        f"💰 Sisa   : {get_user_key(user_id)} Key\n"
                    )
                )
                return

            # --- Koleksi Free Shimmer+ ---
            if need == -1:
                if not (has_shimmer_or_higher(user_id) or is_starlord(user_id)):
                    await cq.answer("❌ Koleksi ini gratis hanya untuk Shimmer 🥉 ke atas!", show_alert=True)
                    return
                mark_vip_unlocked(user_id, code)
                try:
                    meta["origin_msg_id"] = cq.message.id
                    await _send_vip_collection(client, user_id, code, meta, page, sort_by, gratis=False, silent_link=False)
                except Exception as e:
                    await cq.answer(f"❌ Gagal kirim koleksi: {e}", show_alert=True)
                    return

                reply_markup = make_vip_buttons(link, page, sort_by)
                await cq.message.edit_text(
                    f"✅ Koleksi <b>{code}</b> terbuka gratis (Shimmer+).",
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup
                )
                await cq.answer("✅ Koleksi gratis Shimmer terbuka!")

                # 🔹 log shimmer
                await send_vip_log(
                    client,
                    (
                        "✨ <b>FREE UNLOCK (Shimmer+)</b>\n"
                        f"👤 User   : @{username} (ID: <code>{user_id}</code>)\n"
                        f"📦 Kode   : {code}\n"
                        "🔑 Dipakai: 0 Key\n"
                        f"💰 Sisa   : {get_user_key(user_id)} Key\n"
                    )
                )
                return

            # --- Koleksi Free Stellar+ ---
            if need == 0:
                if not (has_stellar_or_higher(user_id) or is_starlord(user_id)):
                    await cq.answer("❌ Koleksi ini gratis hanya untuk Stellar 🥈 ke atas!", show_alert=True)
                    return
                mark_vip_unlocked(user_id, code)
                try:
                    meta["origin_msg_id"] = cq.message.id
                    await _send_vip_collection(client, user_id, code, meta, page, sort_by, gratis=False, silent_link=False)
                except Exception as e:
                    await cq.answer(f"❌ Gagal kirim koleksi: {e}", show_alert=True)
                    return

                reply_markup = make_vip_buttons(link, page, sort_by)
                await cq.message.edit_text(
                    f"✅ Koleksi <b>{code}</b> terbuka gratis (Stellar+).",
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup
                )
                await cq.answer("✅ Koleksi gratis Stellar terbuka!")

                # 🔹 log stellar
                await send_vip_log(
                    client,
                    (
                        "🌟 <b>FREE UNLOCK (Stellar+)</b>\n"
                        f"👤 User   : @{username} (ID: <code>{user_id}</code>)\n"
                        f"📦 Kode   : {code}\n"
                        "🔑 Dipakai: 0 Key\n"
                        f"💰 Sisa   : {get_user_key(user_id)} Key\n"
                    )
                )
                return

            # --- Koleksi Normal ---
            saldo = get_user_key(user_id)
            if saldo < need:
                await cq.answer("⚠️ Saldo Key kurang. Top up lewat /qris.", show_alert=True)
                return

            if not _deduct_user_key_no_lock(user_id, need):
                await cq.answer("❌ Gagal memotong saldo Key.", show_alert=True)
                return

            mark_vip_unlocked(user_id, code)
            sisa = get_user_key(user_id)

            # 🔹 Ambil owner_id dari meta
            owner_id = meta.get("owner_id")

            # 🔔 Notifikasi & reward ke owner
            if owner_id and owner_id != user_id:
                add_user_key(owner_id, 1)
                try:
                    await client.send_message(
                        owner_id,
                        f"🎉 Koleksimu <b>{code}</b> baru saja di-unlock oleh member.\n"
                        f"🎁 Kamu mendapat <b>+1 Key</b> otomatis!",
                        parse_mode=ParseMode.HTML
                    )
                except Exception as e:
                    logger.warning(f"[VIP_NOTIFY_OWNER_FAIL] {e}")

            # 🔹 REWARD OWNER
                try:
                    await send_vip_log(
                        client,
                        (
                            "💎 <b>UNLOCK REWARD</b>\n"
                            f"👤 User   : @{username} (ID: <code>{user_id}</code>)\n"
                            f"📦 Kode   : {code}\n"
                            f"🎁 Owner  : <code>{owner_id}</code> dapat +1 Key"
                        )
                    )
                except Exception as e:
                    logger.error(f"[UNLOCK_REWARD_LOG_ERROR] {e}")

            try:
                await _send_vip_collection(client, user_id, code, meta, page, sort_by)
            except Exception as e:
                _add_user_key_no_lock(user_id, need)  # refund
                await cq.answer(f"❌ Gagal kirim koleksi, saldo direfund: {e}", show_alert=True)
                return
            
            # 🔹 log normal unlock
            await send_vip_log(
                client,
                (
                    "✅ <b>VIP UNLOCKED</b>\n"
                    f"👤 User   : @{username} (ID: <code>{user_id}</code>)\n"
                    f"📦 Kode   : {code}\n"
                    f"🔑 Dipakai: {need} Key\n"
                    f"💰 Sisa   : {sisa} Key\n"
                )
            )

            reply_markup = make_vip_buttons(link, page, sort_by)
            try:
                await cq.message.edit_caption(
                    caption=(
                        "┏━━━━━━━━━━━━━━━┓\n"
                        "   ✅ <b>Akses Berhasil</b>\n"
                        "┗━━━━━━━━━━━━━━━┛\n"
                        "<pre>"
                        f"📦 Kode Koleksi : <b>{code}</b>\n"
                        f"🔑 Pemakaian   : -{need} Key\n"
                        f"💳 Sisa Saldo  : <b>{sisa} Key</b>\n"
                        "</pre>"
                        "✨ Koleksi ini sekarang <b>milikmu</b>.\n"
                        "🔓 Bisa diakses kapan saja tanpa biaya tambahan."
                    ),
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup
                )
            except Exception:
                await cq.message.edit_text(
                    (
                        "┏━━━━━━━━━━━━━━━┓\n"
                        "   ✅ <b>Akses Berhasil</b>\n"
                        "┗━━━━━━━━━━━━━━━┛\n"
                        "<pre>"
                        f"📦 Kode Koleksi : <b>{code}</b>\n"
                        f"🔑 Pemakaian   : -{need} Key\n"
                        f"💳 Sisa Saldo  : <b>{sisa} Key</b>\n"
                        "</pre>"
                        "✨ Koleksi ini sekarang <b>milikmu</b>.\n"
                        "🔓 Bisa diakses kapan saja tanpa biaya tambahan."
                    ),
                    parse_mode=ParseMode.HTML,
                    reply_markup=reply_markup
                )
           
            # tambahin XP user (gunakan cq.from_user biar ke-detect usernya)
            try:
                dummy_msg = type("obj", (), {"from_user": cq.from_user, "reply_text": cq.message.reply_text})
                await grant_xp_for_command(client, dummy_msg, f"unlock_{code}", xp_increment=1)
            except Exception as e:
                logger.error(f"[XP_UNLOCK_ERROR] {e}")

            await cq.answer("✅ Koleksi berhasil dibuka!")
        
            try:
                await send_public_log(
                    client,
                    event="unlock",
                    badge=resolve_badge(user_id),
                    extra=f"{code} (-{need} key)",
                    thumb=resolve_thumb(meta.get("thumbnail"))
                )
            except Exception as e:
                logger.error(f"[VIP_UNLOCK_LOG_ERROR] {e}")
            return

        except Exception as exc:
            await cq.answer(f"❌ Terjadi error: {exc}", show_alert=True)
            return


# --- Search ---
@app.on_callback_query(filters.regex(r"^listvip_search$"))
async def cb_listvip_search(client, cq: CallbackQuery):
    user_id = cq.from_user.id

    if not has_shimmer_or_higher(user_id):
        await cq.answer(
            "❌ Fitur pencarian hanya bisa diakses mulai dari badge Shimmer 🥉 ke atas.",
            show_alert=True
        )
        return

    await cq.answer()
    await cq.message.reply(
        "🔎 <b>Silakan ketik perintah:</b>\n\n"
        "<code>/search kata_kunci</code>\n\n"
        "Contoh: <code>/search angel</code>",
        parse_mode=ParseMode.HTML
    )

# --- Tutup ---
@app.on_callback_query(filters.regex(r"^listvip_close$"))
async def cb_listvip_close(client, cq: CallbackQuery):
    try:
        await cq.message.delete()
    except Exception:
        try:
            await cq.message.edit_text("❌ Ditutup.")
        except Exception:
            pass
    await cq.answer()

# ===============================
# ==SEARCH COMMAND VIP
# ===============================
SEARCH_RESULTS = {}

@app.on_message(filters.command("search") & filters.private)
@require_membership(callback_data="verify_search")
async def search_command(client, message: Message):
    await grant_xp_for_command(client, message, "search")
    user_id = message.from_user.id

    # akses hanya untuk starlord atau admin/owner
    if not (is_owner(message) or is_admin(message) or has_shimmer_or_higher(user_id)):
        teks = (
            "┏━━━━━━━━━━━━━━━━━━━━\n"
            "┃ ❌ <b>AKSES DITOLAK</b>\n"
            "┗━━━━━━━━━━━━━━━━━━━━\n\n"
            "🚫 Fitur ini hanya tersedia untuk pengguna dengan <b>badge tingkat lanjut</b>:\n"
            "🥉 <b>Shimmer</b>\n"
            "🥈 <b>Stellar</b>\n"
            "🥇 <b>Starlord</b>\n\n"
            "👉 Cara mendapatkannya:\n"
            "Gunakan <code>/profile</code> untuk cek XP & badge kamu.\n"
            "Naikkan levelmu step by step:\n\n"
            "🔰 Stranger → 🥉 Shimmer → 🥈 Stellar → 🥇 Starlord\n\n"
            "🚀 Setelah mencapai Shimmer, fitur ini otomatis aktif."
        )
        await message.reply(teks, parse_mode=ParseMode.HTML)
        return

    if len(message.command) < 2:
        await message.reply("ℹ️ Contoh: `/search angel`", parse_mode=ParseMode.MARKDOWN)
        return

    query = " ".join(message.command[1:]).strip()
    if len(query) < 3:
        await message.reply("❌ Kata kunci minimal 3 huruf.", parse_mode=ParseMode.MARKDOWN)
        return

    found = search_codes(query)
    if not found:
        await message.reply(f"❌ Tidak ada koleksi cocok dengan `{query}`.", parse_mode=ParseMode.MARKDOWN)
        return

    SEARCH_RESULTS[user_id] = found
    await show_search_results(message, user_id, query, page=0)

# --- Show Search Results ---
async def show_search_results(message_or_cq, user_id: int, query: str, page: int = 0):
    results = SEARCH_RESULTS.get(user_id, [])
    if not results:
        if isinstance(message_or_cq, CallbackQuery):
            await message_or_cq.answer("❌ Hasil pencarian tidak ada.", show_alert=True)
        else:
            await message_or_cq.reply("❌ Hasil pencarian tidak ada.")
        return

    codes = results
    total_pages = max(1, (len(codes) + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    start = page * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE

    rows = []
    for code in codes[start:end]:
        meta = VIP_COLLECTIONS.get(code, {}) or VIP_MAP.get(code, {})
        need = parse_need(meta.get("keys_required", 1))

        if need == -1:
            label = f"🥉 {code} • 0 🔑"
        elif need == 0:
            label = f"🥈 {code} • 0 🔑"
        elif need <= 3:
            label = f"⭐ {code} • {need} 🔑"
        elif need <= 5:
            label = f"👑 {code} • {need} 🔑"
        else:
            label = f"🔥 {code} • {need} 🔑"

        if has_vip_unlocked(user_id, code):  # ✅ pakai await kalau async
            label = f"✅ {label}"

        # khusus search → pakai callback berbeda biar aman
        rows.append([InlineKeyboardButton(
            label,
            callback_data=f"vip_detail|{code}|{page}|search"
        )])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"search_page|{quote_plus(query)}|{page-1}"))
    nav.append(InlineKeyboardButton(f"📖 {page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"search_page|{quote_plus(query)}|{page+1}"))

    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("❌ Tutup", callback_data="listvip_close")])

    keyboard = InlineKeyboardMarkup(rows)
    teks = f"✨ Hasil pencarian untuk: <b>{query}</b>\nMenemukan <b>{len(results)}</b> item."

    new_msg = await safe_edit(message_or_cq, teks, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    if new_msg:
        FREEKEY_SESSIONS.setdefault(user_id, {})["last_msg_id"] = new_msg.id

# --- Callback untuk pagination ---
@app.on_callback_query(filters.regex(r"^search_page\|(.+?)\|(\d+)$"))
async def cb_search_page(client, cq: CallbackQuery):
    try:
        _, query_enc, page_str = cq.data.split("|")
        query = unquote_plus(query_enc)  # decode aman
        page = int(page_str)
    except Exception:
        await cq.answer("❌ Data tidak valid.", show_alert=True)
        return

    await show_search_results(cq, cq.from_user.id, query, page)
    await cq.answer()

# ===========================
# ==== ADDVIP COMMAND ====
# ===========================
VIP_CAPTION_TEMPLATE_1 = (
    "👑 **VIP COLLECTION** 👑\n"
    "━━━━━━━━━━━━━━\n"
    "💦 Kode: ||{kode}||\n"
    "🗂️ #Kolpri **Koleksi Pribadi Full**\n\n"
    "👉 [LANGSUNG CEK]({link}) 👈\n\n"
    "**SILAHKAN RITUAL KENIKMATAN!**\n"
    "⛔️ **Butuh Bantuan?**\n"
    "💌 [Lapor ke Admin](https://t.me/BangsaBacol_Bot?start=lapor) | "
    "📜 [Daftar Bantuan](https://t.me/BangsaBacol/8)"
)

CAPTION_BROADCAST = (
    "👑 **KOLEKSI EKSKLUSIF!** 👑\n"
    "━━━━━━━━━━━━━━\n"
    "💦 Kode: ||**{kode}**||\n"
    "📷 Total: ||**{media_count}+ Media**||\n"
    "🗂 #Kolpri **Foto & Video Full**\n\n"
    "🤖 **AKSES VIA BOT:**\n"
    "╰─ **@BangsaBacolBot**\n"
    "⚙ **MENU PERINTAH:**\n"
    "╰─ `/listvip`\n\n"
    "✅ Daftar koleksi lengkap dan ter-update!\n"
    "🎁 Jangan lupa `/claim` **hadiah gratis!**\n"
    "━━━━━━━━━━━━━━\n"
    "⛔️ **Butuh Bantuan?**\n"
    "💌 [Lapor ke Admin](https://t.me/BangsaBacol_Bot?start=lapor) | "
    "📜 [Daftar Bantuan](https://t.me/BangsaBacol/8)"
)

@app.on_message(filters.command("addvip") & filters.private)
async def addvip_command(client, message):
    user_id = message.from_user.id
    if not is_owner(message) and not is_admin(message):
        return await message.reply("❌ Kamu tidak punya izin menambahkan VIP.")

    try:
        parts = message.text.split()
        if len(parts) < 5:
            return await message.reply(
                "⚠️ Format salah!\n\n"
                "Gunakan:\n"
                "`/addvip <kode> <link> <keys_required> <media_count>`",
                parse_mode=ParseMode.MARKDOWN
            )

        _, kode, link, *rest = parts
        if len(rest) == 3:
            _thumb_placeholder, keys_required_str, media_count_str = rest
        elif len(rest) == 2:
            keys_required_str, media_count_str = rest
        else:
            return await message.reply("⚠️ Format salah! Jumlah argumen tidak sesuai.")

        keys_required = int(keys_required_str)
        try:
            media_count = int(media_count_str)
        except ValueError:
            media_count = media_count_str

        # ✅ Cek apakah kode sudah ada
        if kode in VIP_MAP:
            buttons = InlineKeyboardMarkup(
                [[
                    InlineKeyboardButton("✅ Update", callback_data=f"updatevip:{kode}:{link}:{keys_required}:{media_count}"),
                    InlineKeyboardButton("❌ Batal", callback_data="cancelvip")
                ]]
            )
            return await message.reply(
                f"⚠️ Kode `{kode}` sudah ada di database.\n"
                f"🖼️ Mau update data & thumbnail?\n\n"
                f"🔗 Link Lama: {VIP_MAP[kode]['link']}\n"
                f"📷 Media Lama: {VIP_MAP[kode]['media_count']}",
                reply_markup=buttons,
                parse_mode=ParseMode.MARKDOWN
            )

        await process_addvip(client, message, kode, link, keys_required, media_count)

    except Exception as e:
        await message.reply(f"❌ Gagal menambahkan VIP: `{e}`")

# ================================
# Tambah / Update Koleksi VIP
# ================================
async def process_addvip(client, message, kode, link, keys_required, media_count):
    # ✅ Tentukan nama file thumbnail
    thumb_filename = f"{kode}.jpg"
    thumb_path = THUMB_DIR / thumb_filename  # THUMB_DIR sebaiknya sudah Path(IMG_DIR)

    # ✅ Kalau ada reply foto → simpan foto baru
    if message.reply_to_message and message.reply_to_message.photo:
        downloaded_path = await message.reply_to_message.download(file_name=str(thumb_path))
        thumb_filename = os.path.basename(downloaded_path)  # ⬅️ paksa jadi nama file
        thumb_path = THUMB_DIR / thumb_filename
    else:
        # ✅ Kalau tidak ada reply foto → pakai thumbnail lama
        if kode in VIP_MAP and VIP_MAP[kode].get("thumbnail"):
            # ambil nama file lama (bukan path absolut)
            thumb_filename = os.path.basename(VIP_MAP[kode]["thumbnail"])
            thumb_path = THUMB_DIR / thumb_filename
        else:
            return await message.reply(
                "❌ Kamu harus reply ke foto thumbnail (data lama tidak ada)."
            )

    # ✅ Simpan / overwrite data VIP hanya dengan nama file
    VIP_MAP[kode] = {
        "link": link,
        "thumbnail": thumb_filename,  # ⬅️ hanya nama file
        "keys_required": keys_required,
        "media_count": str(media_count),
        "konten": "Full Kolpri Premium"
    }
    save_vip_map()

    # ✅ Balasan ke admin
    await message.reply(
        f"✅ Koleksi VIP berhasil ditambahkan/diperbarui!\n\n"
        f"🔑 Kode: `{kode}`\n"
        f"🔗 Link: {link}\n"
        f"🖼️ Thumb: {'baru' if message.reply_to_message and message.reply_to_message.photo else 'lama'}\n"
        f"📷 Media: {media_count}\n"
        f"📦 Konten: Full Kolpri Premium",
        parse_mode=ParseMode.MARKDOWN
    )

    # ✅ Rakit path absolut untuk pengiriman foto
    thumb_to_send = get_thumb_path(thumb_filename)

    # ✅ Kirim ke Channel Publik
    GROUP_ID = -1002806851234
    caption_public = CAPTION_BROADCAST.format(kode=kode, media_count=media_count)

    if thumb_to_send:
        try:
            await client.send_photo(
                chat_id=GROUP_ID,
                photo=thumb_to_send,
                caption=caption_public,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            await message.reply(f"⚠️ Gagal kirim ke Channel Publik: `{e}`")

    await asyncio.sleep(0.5)

    # ✅ Kirim ke Channel VIP
    VIP_CHANNEL_ID = -1002815620251
    caption_vip = VIP_CAPTION_TEMPLATE_1.format(kode=kode, link=link)

    if thumb_to_send:
        try:
            await client.send_photo(
                chat_id=VIP_CHANNEL_ID,
                photo=thumb_to_send,
                caption=caption_vip,
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception as e:
            await message.reply(f"⚠️ Gagal kirim ke Channel VIP: `{e}`")

    # ✅ Tambahkan log publik
    await send_public_log(
        client,
        "addvip",
        badge="ADMIN-PUSAT 🛡",
        extra=f"{kode} ({media_count}+ media)",
        thumb=get_thumb_path(thumb_filename)
    )

# ================================
# Callback Update VIP
# ================================
@app.on_callback_query(filters.regex("^updatevip:"))
async def updatevip_callback(client, callback_query):
    parts = callback_query.data.split(":")

    # Harus 4 bagian: updatevip:KODE:KEYS:MEDIA
    if len(parts) != 4:
        return await callback_query.answer("⚠️ Format update salah.", show_alert=True)

    _, kode, keys_required_str, media_count_str = parts

    if kode not in VIP_MAP:
        return await callback_query.answer("⚠️ Data VIP tidak ditemukan.", show_alert=True)

    # Parsing keys_required
    try:
        keys_required = int(keys_required_str)
    except ValueError:
        keys_required = VIP_MAP[kode]["keys_required"]

    # Parsing media_count
    try:
        media_count = int(media_count_str)
    except ValueError:
        media_count = VIP_MAP[kode]["media_count"]

    # Link lama tetap dipakai
    link = VIP_MAP[kode]["link"]

    # Proses simpan ulang
    await process_addvip(client, callback_query.message, kode, link, keys_required, media_count)
    await callback_query.answer("✅ Data VIP berhasil diperbarui!", show_alert=True)

# ================================
# Callback Cancel VIP
# ================================
@app.on_callback_query(filters.regex("^cancelvip$"))
async def cancelvip_callback(client, callback_query):
    await callback_query.answer("❌ Update dibatalkan", show_alert=True)
    await callback_query.message.delete()

@app.on_message(filters.command("delvip") & filters.private)
async def delvip_command(client, message):
    if not is_owner(message) and not is_admin(message):
        await message.reply("🚫 Kamu tidak punya izin untuk hapus koleksi VIP.")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply("❌ Format salah.\nGunakan: `/delvip kode`", parse_mode=ParseMode.MARKDOWN)
        return

    kode = args[1].strip()
    load_vip_map()
    if kode not in VIP_MAP:
        await message.reply(f"⚠️ Kode <b>{kode}</b> tidak ditemukan.", parse_mode=ParseMode.HTML)
        return

    del VIP_MAP[kode]
    if save_vip_map():
        await message.reply(f"🗑 Koleksi VIP <b>{kode}</b> sudah dihapus.", parse_mode=ParseMode.HTML)
    else:
        await message.reply("❌ Gagal menyimpan perubahan.")

@app.on_message(filters.command("delvip") & filters.private)
async def delvip_command(client, message):
    if not is_owner(message) and not is_admin(message):
        await message.reply("🚫 Kamu tidak punya izin untuk hapus koleksi VIP.")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply("❌ Format salah.\nGunakan: `/delvip kode`", parse_mode=ParseMode.MARKDOWN)
        return

    kode = args[1].strip()
    load_vip_map()
    if kode not in VIP_MAP:
        await message.reply(f"⚠️ Kode <b>{kode}</b> tidak ditemukan.", parse_mode=ParseMode.HTML)
        return

    del VIP_MAP[kode]
    if save_vip_map():
        await message.reply(f"🗑 Koleksi VIP <b>{kode}</b> sudah dihapus.", parse_mode=ParseMode.HTML)
    else:
        await message.reply("❌ Gagal menyimpan perubahan.")

# ===============================
# Collection VIP
# ================================
COLLECT_STEPS = {}  # {admin_id: {...}}

@app.on_message(filters.command("collectvip") & filters.private)
async def cmd_collectvip_start(client, message):
    admin = message.from_user.id
    if not (is_owner(message) or is_admin(message)):
        return await message.reply("❌ Hanya Owner/Admin yang boleh memulai collectvip.")

    # kalau ada session lama → reset biar nggak stuck
    if admin in COLLECT_STEPS:
        COLLECT_STEPS.pop(admin, None)
        print(f"[COLLECTVIP RESET] session lama {admin} dibersihkan otomatis")

    COLLECT_STEPS[admin] = {"step": 0, "files": [], "thumbnail": None, "ts": time.time()}
    await message.reply(
        "📥 Session collectvip dimulai!\n"
        "📸 Kirim/forward media (foto/video/dokumen) satu per satu.\n\n"
        "Jika sudah selesai upload, ketik /finish_collect untuk lanjut step input data.\n\nShortcut:\n/abort_collect untuk batal\n/reset_collect untuk clear session",
        parse_mode=ParseMode.MARKDOWN
    )

@app.on_message(filters.command("abort_collect") & filters.private)
async def cmd_abort_collect(client, message):
    admin = message.from_user.id
    if admin in COLLECT_STEPS:
        COLLECT_STEPS.pop(admin, None)
        await message.reply("✅ Session collectvip dibatalkan.")
    else:
        await message.reply("⚠️ Tidak ada session aktif.")

# CollectVIP handler → group=2 biar jalan setelah FreeKey
@app.on_message(filters.private & (filters.photo | filters.video | filters.document), group=2)
async def collectvip_media_handler(client, message):
    admin = message.from_user.id
    if admin not in COLLECT_STEPS:
        return

    sess = COLLECT_STEPS[admin]

    # =========================
    # MODE 1: Forward dari CHANNEL_VIP
    # =========================
    if message.forward_from_chat and message.forward_from_chat.id == CHANNEL_VIP:
        mid = message.forward_from_message_id
        sess["files"].append(mid)
        if not sess.get("thumbnail"):
            if message.photo:
                sess["thumbnail"] = message.photo.file_id
            elif message.video:
                sess["thumbnail"] = message.video.file_id
            elif message.document:
                sess["thumbnail"] = message.document.file_id
        print(f"[COLLECTVIP HYBRID] Forward → mid={mid}")
        return await message.reply(f"✅ Disimpan dari forward (total: {len(sess['files'])}).\n\nShortcut:\n/collectvip untuk mulai\n/finish_collect untuk selesai\n/abort_collect untuk batal\n/reset_collect untuk clear session")

    # =========================
    # MODE 2: Upload langsung ke BOT
    # =========================
    try:
        sent = await message.copy(CHANNEL_VIP)
        mid = getattr(sent, "id", None)

        if mid:
            sess["files"].append(mid)
            if not sess.get("thumbnail"):
                if message.photo:
                    sess["thumbnail"] = message.photo.file_id
                elif message.video:
                    sess["thumbnail"] = message.video.file_id
                elif message.document:
                    sess["thumbnail"] = message.document.file_id
            print(f"[COLLECTVIP HYBRID] Copy → mid={mid}")
            await message.reply(f"✅ Disimpan dari upload (total: {len(sess['files'])}).")
        else:
            await message.reply("⚠️ Media masuk, tapi tidak bisa ambil message_id.")

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        print(f"[COLLECTVIP ERROR] {e}\n{tb}")
        await message.reply(f"⚠️ Error copy media: {e}")

@app.on_message(filters.command("finish_collect") & filters.private)
async def cmd_finish_collect(client, message):
    admin = message.from_user.id
    if admin not in COLLECT_STEPS:
        return await message.reply("⚠️ Tidak ada session collectvip aktif.")

    sess = COLLECT_STEPS[admin]
    if not sess["files"]:
        return await message.reply("⚠️ Kamu belum upload media apa pun.")

    sess["step"] = 1
    await message.reply("📝 Masukkan kode koleksi:")

@app.on_message(filters.private & filters.text & ~filters.regex(r"^/"), group=3)
async def collectvip_step_handler(client, message):
    admin = message.from_user.id
    if admin not in COLLECT_STEPS:
        return

    sess = COLLECT_STEPS[admin]
    print(f"[COLLECTVIP DEBUG] step={sess.get('step')} text={message.text}")

    # Step 1 → kode
    if sess["step"] == 1:
        sess["kode"] = message.text.strip().lower()
        sess["step"] = 2
        await message.reply("🔑 Masukkan harga (keys_required):")
        await message.stop_propagation()
        return

    # Step 2 → harga
    if sess["step"] == 2:
        raw = message.text.strip()
        sess["raw_keys"] = raw  # simpan raw biar gak hilang
        sess["keys_required"] = normalize_keys_required(raw)
        sess["step"] = 3
        await message.reply("📷 Masukkan jumlah media (media_count):")
        await message.stop_propagation()
        return

    # Step 3 → jumlah media + preview
    if sess["step"] == 3:
        try:
            sess["media_count"] = int(message.text.strip())
        except ValueError:
            await message.reply("⚠️ Masukkan angka yang valid.")
            await message.stop_propagation()
            return

        kode = sess["kode"]
        keys_required = sess["keys_required"]   # ✅ sudah ada dari step 2
        media_count = sess["media_count"]
        total_files = len(sess["files"])

        preview_text = (
            "📋 <b>Konfirmasi Koleksi</b>\n\n"
            f"🔑 <b>Kode:</b> <code>{kode}</code>\n"
            f"💰 <b>Harga:</b> {keys_required}\n"
            f"📷 <b>Jumlah Media:</b> {media_count}\n"
            f"📦 <b>File dikumpulkan:</b> {total_files}"
        )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Simpan", callback_data="collectvip_save")],
            [InlineKeyboardButton("❌ Batal", callback_data="collectvip_cancel")]
        ])

        sess["step"] = "confirm"
        await message.reply(preview_text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
        await message.stop_propagation()
        return

# === CALLBACK HANDLER UNTUK SIMPAN / BATAL ===
@app.on_callback_query(filters.regex(r"^collectvip_"))
async def cb_collectvip_confirm(client, cq):
    admin = cq.from_user.id
    if admin not in COLLECT_STEPS:
        return await cq.answer("⚠️ Tidak ada session aktif.", show_alert=True)

    sess = COLLECT_STEPS[admin]

    if cq.data == "collectvip_save":
        kode = sess["kode"]
        files = sess["files"]

        # Normalisasi keys_required (bisa angka atau "?")
        keys_required = sess.get("keys_required", 0)
        keys_required = normalize_keys_required(str(keys_required).strip())

        media_count = sess.get("media_count", 0)

        # Simpan ke VIP_COLLECTIONS
        load_vip_collections()
        VIP_COLLECTIONS[kode] = {
            "files": files,
            "thumbnail": sess.get("thumbnail"),
            "keys_required": keys_required,
            "media_count": media_count,
            "konten": "Full Koleksi (file_id)"
        }
        save_vip_collections()

        COLLECT_STEPS.pop(admin, None)
        await cq.message.edit_text(
            f"✅ Koleksi <b>{kode}</b> berhasil disimpan!\n"
            f"📦 Total file: {len(files)} | 🔑 Harga: {keys_required} | 📸 Media: {media_count}",
            parse_mode=ParseMode.HTML
        )
        await cq.answer("✅ Disimpan!")

            # === Kirim log publik ===
        GROUP_ID = -1002806851234  # ganti dengan channel publikmu
        thumb_to_send = sess.get("thumbnail")

        if keys_required == 0:  # Stellar Free
            caption = (
                "🥈 **KOLEKSI FREE - Stellar** 🥈\n"
                "━━━━━━━━━━━━━━\n"
                f"💦 Kode: ||**{kode}**||\n"
                f"📷 Total: ||**{media_count}+ Media**||\n"
                "🗂 #Kolpri **Foto & Video Full**\n\n"
                "🔑 Gratis khusus untuk badge **Stellar 🥈 ke atas!**\n"
                "━━━━━━━━━━━━━━\n"
                "🤖 **AKSES VIA BOT:**\n"
                "╰─ **@BangsaBacolBot**\n"
                "⚙ **MENU PERINTAH:**\n"
                "╰─ `/listvip`\n"
                "━━━━━━━━━━━━━━\n"
                "⛔️ **Butuh Bantuan?**\n"
                "💌 [Lapor ke Admin](https://t.me/BangsaBacol_Bot?start=lapor) | "
                "📜 [Daftar Bantuan](https://t.me/BangsaBacol/8)"
            )
        elif str(keys_required) == "?":  # Shimmer Free
            caption = (
                "🥉 **KOLEKSI FREE - Shimmer** 🥉\n"
                "━━━━━━━━━━━━━━\n"
                f"💦 Kode: ||**{kode}**||\n"
                f"📷 Total: ||**{media_count}+ Media**||\n"
                "🗂 #Kolpri **Foto & Video Full**\n\n"
                "🔑 Gratis khusus untuk badge **Shimmer 🥉 ke atas!**\n"
                "━━━━━━━━━━━━━━\n"
                "🤖 **AKSES VIA BOT:**\n"
                "╰─ **@BangsaBacolBot**\n"
                "⚙ **MENU PERINTAH:**\n"
                "╰─ `/listvip`\n"
                "━━━━━━━━━━━━━━\n"
                "⛔️ **Butuh Bantuan?**\n"
                "💌 [Lapor ke Admin](https://t.me/BangsaBacol_Bot?start=lapor) | "
                "📜 [Daftar Bantuan](https://t.me/BangsaBacol/8)"
            )
        else:  # VIP Paid
            caption = (
                "👑 **KOLEKSI EKSKLUSIF!** 👑\n"
                "━━━━━━━━━━━━━━\n"
                f"💦 Kode: ||**{kode}**||\n"
                f"📷 Total: ||**{media_count}+ Media**||\n"
                "🗂 #Kolpri **Foto & Video Full**\n\n"
                f"💰 Harga: ||**{keys_required} Key 🔑**||\n"
                "━━━━━━━━━━━━━━\n"
                "🤖 **AKSES VIA BOT:**\n"
                "╰─ **@BangsaBacolBot**\n"
                "⚙ **MENU PERINTAH:**\n"
                "╰─ `/listvip`\n"
                "━━━━━━━━━━━━━━\n"
                "⛔️ **Butuh Bantuan?**\n"
                "💌 [Lapor ke Admin](https://t.me/BangsaBacol_Bot?start=lapor) | "
                "📜 [Daftar Bantuan](https://t.me/BangsaBacol/8)"
            )

        try:
            if thumb_to_send:
                if os.path.exists(thumb_to_send):
                    with open(thumb_to_send, "rb") as f:
                        await client.send_photo(
                            chat_id=GROUP_ID,
                            photo=f,
                            caption=caption,
                            parse_mode=ParseMode.MARKDOWN
                        )
                else:
                    await client.send_photo(
                        chat_id=GROUP_ID,
                        photo=thumb_to_send,
                        caption=caption,
                        parse_mode=ParseMode.MARKDOWN
                    )
            else:
                await client.send_message(
                    chat_id=GROUP_ID,
                    text=caption,
                    parse_mode=ParseMode.MARKDOWN
                )
        except Exception as e:
            await cq.message.reply(f"⚠️ Gagal kirim log publik: {e}")

        # === Catat ke LOG PUBLIK (catatan aktivitas) ===
        await send_public_log(
            client,
            event="collectvip",
            badge="ADMIN-PUSAT 🛡",
            extra=f"{kode} ({media_count}+ file, {keys_required} key)",
            thumb=resolve_thumb(sess.get("thumbnail"))
        )
    
    elif cq.data == "collectvip_cancel":
        kode = sess.get("kode", "-")
        keys_required = sess.get("keys_required", "?")
        media_count = sess.get("media_count", 0)
        thumb_to_send = sess.get("thumbnail")

        COLLECT_STEPS.pop(admin, None)

        await cq.message.edit_text("❌ Session collectvip dibatalkan.")
        await cq.answer("Session dibatalkan!")

ADD_MEDIA_SESSIONS = {}  # {admin_id: {"kode": str, "files": []}}

@app.on_message(filters.command("addmedia") & filters.private)
async def cmd_add_media(client, message):
    admin = message.from_user.id
    if not (is_owner(message) or is_admin(message)):
        return await message.reply("❌ Hanya Owner/Admin yang bisa menambahkan media.")

    parts = message.text.split()
    if len(parts) < 2:
        return await message.reply("⚠️ Format: /addmedia <kode_koleksi>")

    kode = parts[1].strip().lower()
    load_vip_collections()
    if kode not in VIP_COLLECTIONS:
        return await message.reply(f"⚠️ Koleksi `{kode}` tidak ditemukan.")

    ADD_MEDIA_SESSIONS[admin] = {"kode": kode, "files": []}
    await message.reply(
        f"📥 Session tambah media untuk koleksi `{kode}` dimulai!\n"
        "📸 Kirim media (foto/video/dokumen) satu per satu.\n"
        "Jika sudah selesai, ketik /finish_addmedia untuk simpan.\n"
        "Ketik /abort_addmedia untuk batal."
    )

@app.on_message(filters.private & (filters.photo | filters.video | filters.document), group=4)
async def addmedia_handler(client, message):
    admin = message.from_user.id
    if admin not in ADD_MEDIA_SESSIONS:
        return

    sess = ADD_MEDIA_SESSIONS[admin]

    try:
        sent = await message.copy(CHANNEL_VIP)
        mid = getattr(sent, "id", None)
        if mid:
            sess["files"].append(mid)
            await message.reply(f"✅ Media ditambahkan (total session: {len(sess['files'])})")
        else:
            await message.reply("⚠️ Media masuk, tapi gagal ambil message_id.")
    except Exception as e:
        await message.reply(f"⚠️ Gagal upload media: {e}")

@app.on_message(filters.command("finish_addmedia") & filters.private)
async def finish_addmedia(client, message):
    admin = message.from_user.id
    if admin not in ADD_MEDIA_SESSIONS:
        return await message.reply("⚠️ Tidak ada session add media aktif.")

    sess = ADD_MEDIA_SESSIONS.pop(admin)
    kode = sess["kode"]
    files_new = sess["files"]

    load_vip_collections()
    VIP_COLLECTIONS[kode]["files"].extend(files_new)
    VIP_COLLECTIONS[kode]["media_count"] = len(VIP_COLLECTIONS[kode]["files"])
    save_vip_collections()

    await message.reply(
        f"✅ Media baru berhasil ditambahkan ke koleksi `{kode}`.\n"
        f"📦 Total file sekarang: {len(VIP_COLLECTIONS[kode]['files'])}"
    )

@app.on_message(filters.command("abort_addmedia") & filters.private)
async def abort_addmedia(client, message):
    admin = message.from_user.id
    if admin in ADD_MEDIA_SESSIONS:
        ADD_MEDIA_SESSIONS.pop(admin, None)
        await message.reply("❌ Session tambah media dibatalkan.")
    else:
        await message.reply("⚠️ Tidak ada session aktif.")


@app.on_message(filters.command("reset_collect") & filters.private)
async def cmd_reset_collect(client, message):
    admin = message.from_user.id
    COLLECT_STEPS.pop(admin, None)
    await message.reply("♻️ Session collectvip kamu sudah direset manual.")

@app.on_message(filters.command("delcollect") & filters.private)
async def cmd_delcollect(client, message):
    admin = message.from_user.id
    if not (is_owner(message) or is_admin(message)):
        return await message.reply("❌ Hanya Owner/Admin yang bisa menghapus koleksi.")

    parts = message.text.split()
    if len(parts) < 2:
        return await message.reply("⚠️ Format: `/delcollect <kode>`", parse_mode=ParseMode.MARKDOWN)

    kode = parts[1].strip().lower()

    load_vip_collections()
    if kode not in VIP_COLLECTIONS:
        return await message.reply(f"❌ Koleksi `{kode}` tidak ditemukan.", parse_mode=ParseMode.MARKDOWN)

    # simpan backup dulu sebelum dihapus
    deleted = VIP_COLLECTIONS.pop(kode)
    try:
        save_vip_collections()
        await message.reply(
            f"🗑 Koleksi `{kode}` berhasil dihapus.\n"
            f"📦 Total file: {len(deleted.get('files', []))}\n"
            f"🔑 Harga: {deleted.get('keys_required', '?')} Key",
            parse_mode=ParseMode.MARKDOWN
        )
        logger.warning(f"[VIP_COLLECT_DELETE] Koleksi {kode} dihapus oleh {admin}")
    except Exception as e:
        # rollback jika gagal simpan
        VIP_COLLECTIONS[kode] = deleted
        logger.error(f"[VIP_COLLECT_DELETE_ERROR] {e}")
        await message.reply("❌ Gagal menghapus koleksi. Cek log admin.")

# ===============================
# --- Command: /myvip ---
# ===============================

def build_myvip_keyboard(user_id: int, page: int = 0) -> InlineKeyboardMarkup:
    data = load_user_data()
    user = data.get(str(user_id), {})
    unlocked = user.get("vip_unlocked", [])

    if not unlocked:
        return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Belum ada koleksi", callback_data="noop")]])

    total_items = len(unlocked)
    total_pages = max(1, (total_items + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    page = max(0, min(page, total_pages - 1))

    start = page * ITEMS_PER_PAGE
    end = start + ITEMS_PER_PAGE

    rows = []
    for code in unlocked[start:end]:
        # Cek apakah meta masih ada (kode tidak dihapus dari VIP_MAP)
        meta = get_vip_meta(code)
        if not meta:
            label = f"⚠️ {code} (missing)"
            rows.append([InlineKeyboardButton(label, callback_data="noop")])
            continue

        label = f"✅ {meta.get('title', code)}"
        # sertakan page di callback supaya bisa balik ke halaman yang benar
        rows.append([InlineKeyboardButton(label, callback_data=f"myvip_detail|{code}|{page}")])

    # Navigasi halaman
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"myvip_page|{page-1}"))
    nav.append(InlineKeyboardButton(f"📖 {page+1}/{total_pages}", callback_data="noop"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"myvip_page|{page+1}"))
    if nav:
        rows.append(nav)

    rows.append([InlineKeyboardButton("❌ Tutup", callback_data="myvip_close")])
    return InlineKeyboardMarkup(rows)

@app.on_message(filters.command("myvip") & filters.private)
async def myvip_command(client, message: Message):
    await grant_xp_for_command(client, message, "myvip")
    user_id = message.from_user.id
    data = load_user_data()
    user = data.get(str(user_id), {})
    unlocked = user.get("vip_unlocked", [])

    if not unlocked:
        await message.reply("🔑 Kamu belum membuka koleksi VIP.")
        return

    teks = (
        "┏━━━━━━━━━━━━━━━\n"
        "┃ 👑 <b>Koleksi VIP Kamu</b>\n"
        "┗━━━━━━━━━━━━━━━\n"
        "📂 Semua koleksi yang pernah kamu buka tersimpan aman.\n"
        "✨ Klik untuk akses ulang tanpa biaya tambahan."
    )
    keyboard = build_myvip_keyboard(user_id, 0)

    new_msg = await message.reply(teks, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    FREEKEY_SESSIONS.setdefault(user_id, {})["last_msg_id"] = new_msg.id  # simpan pesan terakhir

    username = message.from_user.username or "NoUsername"
    jumlah = len(unlocked)
    daftar = ", ".join(unlocked)

    await send_vip_log(
        client,
        (
            "👑 <b>MY VIP OPENED</b>\n"     
            f"👤 User   : @{username} (ID: <code>{user_id}</code>)\n"
            f"📦 Total  : {jumlah}\n"
            f"📂 Koleksi: {daftar or '-'}\n"    
        )
    )

    try:
        await send_public_log(
            client,
            "myvip",
            badge=user.get("badge", "Stranger 🔰"),
            extra=f"{jumlah} koleksi"
        )
    except Exception as e:
        logger.error(f"[MYVIP_PUBLIC_LOG_ERROR] {e}")

# --- Detail Koleksi MyVIP ---
@app.on_callback_query(filters.regex(r"^myvip_detail\|(.+?)\|(\d+)$"))
async def cb_myvip_detail(client, cq: CallbackQuery):
    try:
        _, code, page_str = cq.data.split("|")
        page = int(page_str)
    except Exception:
        await cq.answer("❌ Data tidak valid.", show_alert=True)
        return

    meta = get_vip_meta(code)
    if not meta:
        await cq.answer("⚠️ Koleksi tidak ditemukan.", show_alert=True)
        return

    source = meta.get("source", "link")
    title = meta.get("title", code)

    caption = (
        "┏━━━━━━━━━━━━━\n"
        "┃ 👑 <b>KOLEKSI VIP</b>\n"
        "┗━━━━━━━━━━━━━\n"
        f"🔒 <b>Kode</b>   : <code>{code}</code>\n"
        f"📂 <b>Jenis</b> : {'Link' if source=='link' else 'Media'}\n"
        "✅ Koleksi ini sudah kamu miliki!\n"
        "📂 Klik tombol di bawah untuk akses ulang."
    )

    # ==== Keyboard sesuai jenis koleksi ====
    if source == "link" and meta.get("link"):
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Buka Koleksi", url=meta["link"])],
            [InlineKeyboardButton("⬅️ Kembali", callback_data=f"myvip_page|{page}")]
        ])

        # 🔗 Link → cukup edit pesan lama
        await safe_edit(cq, caption, reply_markup=kb, parse_mode=ParseMode.HTML)

    else:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔓 Akses Ulang", callback_data=f"vip_unlock|{code}|0|{page}|default")],
            [InlineKeyboardButton("⬅️ Kembali", callback_data=f"myvip_page|{page}")]
        ])

        try:
            thumb = resolve_thumb(meta.get("thumbnail"))
            if thumb:
                new_msg = await cq.message.reply_photo(
                    photo=thumb,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb
                )
            else:
                new_msg = await cq.message.reply_text(
                    f"🖼️ {title}\n\n{caption}",
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb
                )

            if new_msg:
                FREEKEY_SESSIONS.setdefault(cq.from_user.id, {})["last_msg_id"] = new_msg.id

            # hapus pesan lama hanya kalau bikin pesan baru
            try:
                await cq.message.delete()
            except:
                pass

        except Exception as e:
            logger.error(f"[MYVIP_DETAIL_ERROR] {e}")
            await cq.answer(f"Terjadi error: {e}", show_alert=True)


@app.on_callback_query(filters.regex(r"^myvip_page\|(\d+)$"))
async def cb_myvip_page(client, cq: CallbackQuery):
    user_id = cq.from_user.id
    try:
        page = int(cq.data.split("|")[1])
    except (IndexError, ValueError):
        await cq.answer("❌ Data tidak valid.", show_alert=True)
        return

    keyboard = build_myvip_keyboard(user_id, page)

    teks = (
        "┏━━━━━━━━━━━━━━━\n"
        "┃ 👑 <b>Koleksi VIP Kamu</b>\n"
        "┗━━━━━━━━━━━━━━━\n"
        "📂 Semua koleksi yang pernah kamu buka tersimpan aman.\n"
        "✨ Klik untuk akses ulang tanpa biaya tambahan."
    )

    new_msg = await safe_edit(cq, teks, reply_markup=keyboard, parse_mode=ParseMode.HTML)

    if new_msg:
        FREEKEY_SESSIONS.setdefault(user_id, {})["last_msg_id"] = new_msg.id

    await cq.answer()

@app.on_callback_query(filters.regex(r"^myvip_close$"))
async def cb_myvip_close(client, cq: CallbackQuery):
    try:
        await cq.message.delete()
    except Exception:
        try:
            await cq.message.edit_text("❌ Ditutup.")
        except Exception:
            pass
    await cq.answer()

from pyrogram.enums import ParseMode

@app.on_message(filters.command("setowner") & filters.private)
async def set_owner_cmd(client, message):
    user_id = message.from_user.id
    args = message.text.split()

    # Minimal harus ada kode
    if len(args) < 2:
        await message.reply(
            "⚠️ Format salah.\n"
            "Gunakan:\n"
            "`/setowner <kode_koleksi>` (untuk diri sendiri)\n"
            "`/setowner <kode_koleksi> <user_id>` (untuk orang lain)",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    code = args[1].strip()
    target_id = user_id  # default = dirinya sendiri

    # kalau ada argumen ketiga, berarti admin mau set untuk orang lain
    if len(args) >= 3:
        if not (is_owner(message) or is_admin(message) or is_starlord(user_id)):
            await message.reply("❌ Kamu tidak punya izin untuk set owner orang lain.", parse_mode=ParseMode.MARKDOWN)
            return
        try:
            target_id = int(args[2])
        except ValueError:
            await message.reply("❌ user_id tidak valid.", parse_mode=ParseMode.MARKDOWN)
            return

    meta = None
    source = None

    # cek di VIP_MAP
    if code in VIP_MAP:
        meta = VIP_MAP[code]
        source = "map"
    elif code in VIP_COLLECTIONS:
        meta = VIP_COLLECTIONS[code]
        source = "collections"

    if not meta:
        await message.reply(f"❌ Koleksi dengan kode `{code}` tidak ditemukan.", parse_mode=ParseMode.MARKDOWN)
        return

    # set owner_id
    meta["owner_id"] = target_id

    # simpan
    if source == "map":
        save_vip_map()
    elif source == "collections":
        save_vip_collections()

    await message.reply(
        f"✅ Koleksi `{code}` sekarang terdaftar dengan owner_id = `{target_id}`",
        parse_mode=ParseMode.MARKDOWN
    )

    # kirim notifikasi ke owner baru
    try:
        await client.send_message(
            target_id,
            f"🎉 Selamat! Kamu sekarang menjadi *owner* dari koleksi `{code}`.\n"
            "Setiap ada member yang unlock koleksi ini, kamu akan menerima *+1 Key* otomatis.",
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception:
        # abaikan kalau gagal (misalnya user belum pernah chat bot)
        pass

@app.on_message(filters.command("unsetowner") & filters.private)
async def unset_owner_cmd(client, message):
    user_id = message.from_user.id
    args = message.text.split()

    if len(args) < 2:
        await message.reply(
            "⚠️ Format salah.\n"
            "Gunakan:\n"
            "`/unsetowner <kode_koleksi>`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    code = args[1].strip()
    meta = None
    source = None

    # cek di VIP_MAP
    if code in VIP_MAP:
        meta = VIP_MAP[code]
        source = "map"
    elif code in VIP_COLLECTIONS:
        meta = VIP_COLLECTIONS[code]
        source = "collections"

    if not meta:
        await message.reply(f"❌ Koleksi dengan kode `{code}` tidak ditemukan.", parse_mode=ParseMode.MARKDOWN)
        return

    # hanya owner lama atau admin yang boleh unset
    current_owner = meta.get("owner_id", 0)
    if current_owner not in (0, user_id) and not (is_owner(message) or is_admin(message) or is_starlord(user_id)):
        await message.reply("❌ Kamu tidak punya izin untuk unset owner koleksi ini.", parse_mode=ParseMode.MARKDOWN)
        return

    # simpan owner_id = 0
    meta["owner_id"] = 0

    if source == "map":
        save_vip_map()
    elif source == "collections":
        save_vip_collections()

    await message.reply(
        f"✅ Owner untuk koleksi `{code}` sudah dihapus.",
        parse_mode=ParseMode.MARKDOWN
    )

    # kirim notif ke owner lama (jika ada dan bukan admin yang unset)
    if current_owner and current_owner != 0:
        try:
            await client.send_message(
                current_owner,
                f"⚠️ Kamu tidak lagi menjadi *owner* dari koleksi `{code}`.",
                parse_mode=ParseMode.MARKDOWN
            )
        except Exception:
            pass


# ================================
# SET PROFIL
# ================================
@app.on_message(filters.command("setvip") & filters.private)
async def cmd_setvip(client, message: Message):
    if not (is_owner(message) or is_admin(message)):
        return await message.reply("❌ Hanya Owner/Admin yang boleh pakai command ini.")

    args = message.text.split()
    if len(args) < 2:
        return await message.reply("⚠️ Format: <code>/setvip @username [key]</code>", parse_mode=ParseMode.HTML)

    target = args[1]
    bonus_key = int(args[2]) if len(args) >= 3 and args[2].isdigit() else 0

    try:
        user_obj = await client.get_users(target)
    except Exception as e:
        return await message.reply(f"❌ Gagal menemukan user: {e}")

    user_id = user_obj.id
    username = user_obj.username or "-"
    data = load_user_data()
    user = data.get(str(user_id), {})

    # simpan riwayat lama dulu
    user.setdefault("prev_badge", user.get("badge", BADGE_STRANGER))
    user.setdefault("prev_xp", user.get("xp", 0))

    # update ke Starlord
    user["badge"] = BADGE_STARLORD
    user["xp"] = 9999
    if bonus_key > 0:
        user["key"] = int(user.get("key", 0)) + bonus_key
    data[str(user_id)] = user
    save_user_data(data)

    # notif ke admin
    await message.reply(
        f"✅ @{username} berhasil jadi VIP!\n"
        f"🔖 Badge: {BADGE_STARLORD}\n"
        f"⚡ XP: 9999\n"
        f"🔑 Key: {user.get('key', 0)}",
        parse_mode=ParseMode.HTML
    )

    # notif ke user target
    try:
        teks_user = (
            "┏━━━━━━━━━━━━━━━━━━━━━━\n"
            "┃ 🌟 <b>SELAMAT DATANG VIP!</b>\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 @{username}\n"
            f"Kamu resmi bergabung sebagai member <b>VIP</b> dengan badge:\n"
            "┏━━━━━━━━━━━━━━━━━━━━━━\n"
            f"┃  <b>{BADGE_STARLORD}</b>\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚡ XP: <b>9999</b>\n"
            f"🔑 Bonus Key: <b>{user.get('key', 0)}</b>\n"
            "✨ Nikmati semua fitur spesial, koleksi premium, dan akses eksklusif yang hanya tersedia untuk VIP!\n\n"
            "🙏 Terima kasih sudah mendukung komunitas <b>Bangsa Bacol</b>!"
        )
        await client.send_message(user_id, teks_user, parse_mode=ParseMode.HTML)
    except Exception:
        pass
    
    # optional log
    if LOG_CHANNEL_ID:
        try:
            await client.send_message(LOG_CHANNEL_ID, f"👑 Promote VIP → @{username} jadi {BADGE_STARLORD}")
        except:
            pass
        
    # ✅ log publik anonim
    if PUBLIC_LOG_CHANNEL_ID:
        try:
            await client.send_message(PUBLIC_LOG_CHANNEL_ID, f"👑 WOW! Promote VIP → Stranger 🔰 jadi {BADGE_STARLORD}")
        except:
            pass


@app.on_message(filters.command("unsetvip") & filters.private)
async def cmd_unsetvip(client, message: Message):
    if not (is_owner(message) or is_admin(message)):
        return await message.reply("❌ Hanya Owner/Admin yang boleh pakai command ini.")

    args = message.text.split()
    if len(args) < 2:
        return await message.reply("⚠️ Format: <code>/unsetvip @username</code>", parse_mode=ParseMode.HTML)

    target = args[1]
    try:
        user_obj = await client.get_users(target)
    except Exception as e:
        return await message.reply(f"❌ Gagal menemukan user: {e}")

    user_id = user_obj.id
    username = user_obj.username or "-"
    data = load_user_data()
    user = data.get(str(user_id), {})

    # restore riwayat lama
    prev_badge = user.get("prev_badge", BADGE_STRANGER)
    prev_xp = user.get("prev_xp", 0)
    user["badge"] = prev_badge
    user["xp"] = prev_xp
    user.pop("prev_badge", None)
    user.pop("prev_xp", None)
    data[str(user_id)] = user
    save_user_data(data)

    # notif ke admin
    await message.reply(
        f"🚫 @{username} dicabut VIP nya.\n"
        f"🔖 Badge: {prev_badge}\n"
        f"⚡ XP: {prev_xp}\n"
        f"🔑 Key tetap: {user.get('key',0)}",
        parse_mode=ParseMode.HTML
    )

    # notif ke user
    try:
        teks_user = (
            "┏━━━━━━━━━━━━━━━━━━━━━━\n"
            "┃ ⚠️ <b>STATUS VIP BERAKHIR</b>\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━\n"
            f"👤 @{username}\n"
            "Masa aktif VIP kamu sudah <b>berakhir</b>.\n"
            "┏━━━━━━━━━━━━━━━━━━━━━━\n"
            "┃ 🔖 <b>Status Sekarang</b>\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔖 Badge: <b>{prev_badge}</b>\n"
            f"⚡ XP: <b>{prev_xp}</b>\n"
            f"🔑 Key: <b>{user.get('key', 0)}</b>\n"
            "🙏 Terima kasih sudah pernah menjadi bagian dari VIP.\n\n"
            "✨ Kamu bisa upgrade lagi kapan saja untuk menikmati fitur eksklusif!"
        )
        await client.send_message(user_id, teks_user, parse_mode=ParseMode.HTML)
    except:
        pass
    
    # optional log
    if LOG_CHANNEL_ID:
        try:
            await client.send_message(LOG_CHANNEL_ID, f"👑 MASA AKTIF MEMBER VIP HABIS! → @{username} jadi {prev_badge}")
        except:
            pass
        
    # ✅ log publik anonim
    if PUBLIC_LOG_CHANNEL_ID:
        try:
            await client.send_message(PUBLIC_LOG_CHANNEL_ID, f"👑 YAH! MASA AKTIF MEMBER VIP HABIS! → Starlord 🥇 jadi {prev_badge}")
        except:
            pass

# ================================
# Daftar Bot Mirror
# ================================
BOT_MIRRORS = [
    {"role": "Bot Utama", "name": "🤖 Bangsa Bacol Bot", "username": "BangsaBacolBot"},
    {"role": "Bot Kedua", "name": "🤖 Kolpri Bacol | Seraphina", "username": "BangsaBacol_Bot"},
    {"role": "Bot Ketiga", "name": "🤖 Koleksi Bangsa | Stephander", "username": "Bangsa_BacolBot"},
]

@app.on_message(filters.command("bot"))
@require_membership(callback_data="verify_bot")
async def bot_command(client, message):
    await grant_xp_for_command(client, message, "bot")

    # Tombol → baris 1 (utama), baris 2 (kedua + ketiga)
    buttons = [
        [InlineKeyboardButton("✅ BOT UTAMA", url=f"https://t.me/{BOT_MIRRORS[0]['username']}")],
        [
            InlineKeyboardButton("🤖 SERAPHINA", url=f"https://t.me/{BOT_MIRRORS[1]['username']}"),
            InlineKeyboardButton("🤖 STEPHANDER", url=f"https://t.me/{BOT_MIRRORS[2]['username']}")
        ]
    ]
    kb = InlineKeyboardMarkup(buttons)

    teks = (
        "🤖 <b>DAFTAR BOT RESMI BANGSA BACOL</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🟢 <b>{BOT_MIRRORS[0]['role']}</b> | {BOT_MIRRORS[0]['name']}\n"
        f"➥ @{BOT_MIRRORS[0]['username']}\n\n"
        f"🟡 <b>{BOT_MIRRORS[1]['role']}</b> | {BOT_MIRRORS[1]['name']}\n"
        f"➥ @{BOT_MIRRORS[1]['username']}\n\n"
        f"🔵 <b>{BOT_MIRRORS[2]['role']}</b> | {BOT_MIRRORS[2]['name']}\n"
        f"➥ @{BOT_MIRRORS[2]['username']}\n\n"

        f"Jangan lupa join Channel Cadanngan = {CHANNEL_CADANGAN}\n\n"
        
        "📌 <b>Panduan Pemakaian:</b>\n"
        "• Gunakan 🟢 <b>Bot Utama</b> untuk semua aktivitas normal.\n"
        "• Jika Bot Utama <b>sibuk/error</b>, gunakan 🟡 <b>Bot Kedua</b> sebagai cadangan.\n"
        "• Jika masih terkendala, gunakan 🔵 <b>Bot Ketiga</b>.\n\n"
        "⚠️ <i>Gunakan hanya bot resmi di atas. Jangan percaya pada akun lain yang mengatasnamakan Bangsa Bacol!</i>"
    )

    await message.reply(teks, reply_markup=kb, parse_mode=ParseMode.HTML)

@app.on_message(filters.command("claim") & filters.private)
@require_membership(callback_data="verify_claim")
async def claim_weekly(client, message):
    await grant_xp_for_command(client, message, "claim")
    user_id = message.from_user.id
    can_claim, remaining = can_claim_weekly(user_id)

    if not can_claim:
        days = remaining // 86400
        hours = (remaining % 86400) // 3600
        minutes = (remaining % 3600) // 60
        await message.reply(
            "┏━━━━━━━━━━━━━━━\n"
            "┃ ⏳ <b>CLAIM SUDAH DIAMBIL!</b>\n"
            "┗━━━━━━━━━━━━━━━\n\n"
            f"🕒 Coba lagi dalam: <b>{days}h {hours}j {minutes}m</b>."
        )
        return

    # ✅ tambahkan 2 key
    add_user_key(user_id, 2)
    set_weekly_claim(user_id)

    saldo = get_user_key(user_id)

    await message.reply(
        "┏━━━━━━━━━━━━━━━\n"
        "┃ 🎉 <b>CLAIM BERHASIL!</b>\n"
        "┗━━━━━━━━━━━━━━━\n\n"
        f"✅ Kamu mendapatkan <b>+2 Key</b> gratis minggu ini.\n"
        f"💰 Saldo sekarang: <b>{saldo} Key</b>",
        parse_mode=ParseMode.HTML
    )

@app.on_callback_query(filters.regex(r"^claim_weekly$"))
async def cb_claim_weekly(client, cq: CallbackQuery):
    user_id = cq.from_user.id
    username = cq.from_user.username or "NoUsername"

    # cek apakah sudah bisa klaim
    can_claim, remaining = can_claim_weekly(user_id)
    if not can_claim:
        days = remaining // 86400
        hours = (remaining % 86400) // 3600
        minutes = (remaining % 3600) // 60
        await cq.answer(
            "┏━━━━━━━━━━━━━━━\n"
            "┃ ⏳ <b>CLAIM SUDAH DIAMBIL!</b>\n"
            "┗━━━━━━━━━━━━━━━\n\n"
            f"🕒 Coba lagi dalam: <b>{days}h {hours}j {minutes}m</b>.",
            show_alert=True
        )
        return

    # ✅ Tambahkan 2 Key
    add_user_key(user_id, 2)
    set_weekly_claim(user_id)
    saldo = get_user_key(user_id)

    # update pesan profil user
    teks = (
        "┏━━━━━━━━━━━━━━━\n"
        "┃ 🎉 <b>CLAIM BERHASIL!</b>\n"
        "┗━━━━━━━━━━━━━━━\n\n"
        "🏅 Reward Mingguan\n"
        "✅ +2 Key berhasil ditambahkan!\n"
        f"💰 Saldo sekarang: <b>{saldo} Key</b>"
    )
    try:
        await cq.message.edit_text(teks, parse_mode=ParseMode.HTML)
    except Exception:
        await cq.message.reply(teks, parse_mode=ParseMode.HTML)

    # ✅ log admin
    await send_vip_log(
        client,
        (
            "🎁 <b>WEEKLY CLAIM</b>\n"
            f"👤 User  : @{username} (ID: <code>{user_id}</code>)\n"
            "✨ Reward: +2 Key\n"
            f"💰 Saldo : {saldo} Key"
        )
    )

    # 🚩 log publik anonim
    try:
        # ambil data user untuk badge
        data = load_user_data()
        user_data = data.get(str(user_id), {"badge": "Stranger 🔰"})

        # jumlah koleksi → fallback ke 0 kalau tidak ada
        jumlah = user_data.get("koleksi_count", 0)

        await send_public_log(
            client,
            "claim",
            badge=user_data.get("badge"),
            extra="2 Key"
        )
    except Exception as e:
        logger.error(f"Public log claim_weekly gagal: {e}")


@app.on_message(filters.command("ping"))
async def ping_cmd(client, message):
    await grant_xp_for_command(client, message, "ping")
    await message.reply("✅ Pong! Bot aktif dan responsif.")

@app.on_message(filters.command("random"))
@require_membership(callback_data="verify_random")
async def random_command(client, message):
    # Tambah XP
    await grant_xp_for_command(client, message, "random")

    user_id = message.from_user.id
    log_user_activity(user_id, message.from_user.username or "")

    in_channel = await is_member(client, user_id, CHANNEL_USERNAME)
    in_group   = await is_member(client, user_id, GROUP_USERNAME)
    is_extra_member   = await is_member(client, user_id, EXTRA_CHANNEL)
    if not in_channel or not in_group:
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📢 Channel Utama", url=f"https://t.me/{CHANNEL_USERNAME}")],
            [InlineKeyboardButton("🔁 Channel Backup", url=f"https://t.me/{EXTRA_CHANNEL}")],
            [InlineKeyboardButton("💬 Join Group",   url=f"https://t.me/{GROUP_USERNAME}")]
        ])
        await message.reply_text(
            "⚠️ **TERCYDUK BELUM JOIN! ⚠️**\nKAMU HARUS JOIN GROUP & CHANNEL DULU WAHAI ORANG ASING!",
            reply_markup=keyboard
        )
        return
    
    # === CEK BADGE DI SINI ===
    if not has_shimmer_or_higher(user_id):
        await message.reply_text(
            "❌ <b>Akses Ditolak!</b>\n\n"
            "Perintah ini hanya untuk pengguna dengan badge minimal <b>Shimmer 🥉</b> ke atas.\n"
            "Gunakan /profile untuk cek badge kamu.",
            parse_mode=ParseMode.HTML
        )
        return

    allowed, remaining_after, limit, reset_sec = await consume_random_quota(user_id)
    if not allowed:
        await message.reply_text(
            f"⛔ Jatah harian /random habis.\nLimit {limit}x/hari • Reset { _format_eta(reset_sec) } lagi."
        )
        return

    if not STREAM_MAP:
        await message.reply_text("⚠️ Belum ada koleksi tersedia.")
        return

    valid = []
    for k, v in STREAM_MAP.items():
        if isinstance(v, str) and v.strip():
            valid.append((k, v.strip(), None))
        elif isinstance(v, dict) and v.get("link"):
            valid.append((k, v["link"], v.get("thumbnail")))

    if not valid:
        await message.reply_text("⚠️ Tidak ada koleksi valid.")
        return

    kode, link, thumb = random.choice(valid)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔗 TONTON SEKARANG", url=link)]])
    caption = f"🎲 Koleksi Random\n<b>Kode:</b> <code>{kode}</code>\n<i>Sisa jatah hari ini: {remaining_after}/{limit}</i>"

    # ✅ ambil badge dari user_data
    data = load_user_data()
    user = data.get(str(user_id), {"badge": BADGE_STRANGER})
    await send_public_log(client, "random", badge=user.get("badge"))

    if thumb and Path(f"Img/{thumb}").exists():
        await message.reply_photo(
            photo=f"Img/{thumb}", caption=caption, reply_markup=kb, parse_mode=ParseMode.HTML
        )
    else:
        await message.reply_text(
            caption, reply_markup=kb, parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )

@app.on_message(filters.command("quota"))
async def quota_command(client, message):
    used, remaining, limit, reset_sec = await get_random_quota_status(message.from_user.id)
    await message.reply_text(
        f"📊 Jatah /random kamu hari ini:\nDipakai: {used}\nSisa: {remaining}\nLimit: {limit}\nReset: { _format_eta(reset_sec) } lagi"
    )

# -------------------- ABOUT --------------------
@app.on_message(filters.command("about"))
@require_membership(callback_data="verify_about")
async def about_command(client, message):
    await grant_xp_for_command(client, message, "about")
    teks = """
◢ ℹ️ <b>ABOUT</b> ◣

Hallo Bacolers!
Aku <a href="https://t.me/BangsaBacolBot">@BangsaBacolBot</a>,  
pelayan setia kebangsaan kita! 

Aku diciptakan untuk mengelola <b>Channel Publik Bangsa Bacol</b>,  
serta memberikan akses ke semua koleksi.  
Sedangkan <b>Admin & Menteri</b> aktif di Channel VIP.  

📌 <b>Info Cepat:</b>  
- 📩 Lapor → <a href='https://t.me/BangsaBacol_Bot?start=lapor'>Admin-Pusat</a>
- 📜 Bantuan → <a href='https://t.me/BangsaBacol/8'>Daftar Bantuan</a>
- 🔑 Join VIP → <a href="https://trakteer.id/BangsaBacolers/showcase">Klik di sini</a>  

📢 Channel: <a href="https://t.me/BangsaBacol">@BangsaBacol</a>  
💬 Group: <a href="https://t.me/BangsaBacolGroup">@BangsaBacolGroup</a>
"""
    await message.reply_text(teks, disable_web_page_preview=True, parse_mode=ParseMode.HTML)

# ================================
# JOIN VIP
# ================================
# joinvip_multiplan.py
# Copy this into your bot file (or import it). It implements a 2-step Join VIP flow:
# 1) user picks Lifetime or Monthly
# 2) each path has Trakteer + Saweria buttons and separate logs

from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from pyrogram.enums import ParseMode
from datetime import datetime

# NOTE: this module expects the following names to already exist in your project:
# - app (pyrogram Client)
# - grant_xp_for_command(client, message, command_name)
# - JAKARTA_TZ (a tzinfo) for formatting times
# If they live in another module, import them accordingly.

# ------------------ CONFIG ------------------
ADMIN_MENTION = ["@mrandalan", "@queencwans", "@lolicwans"]
VIP_SESSIONS = {}
LOG_CHANNEL = -1002316200587

# Prices (edit to match your real prices)
LIFETIME_PRICE = "200K"
MONTHLY_PRICE = "100K"

# --------------------------------------------

def format_admin_mentions() -> str:
    """Gabungkan semua admin jadi satu string mention."""
    if not ADMIN_MENTION:
        return "@admin"
    return " ".join(ADMIN_MENTION)


def _initial_caption() -> str:
    return (
        "┏━━━━━━━━━━━━━━━━━\n"
        "┃ 🌟 <b>BANGSA BACOL VIP</b> 🌟\n"
        "┗━━━━━━━━━━━━━━━━━\n\n"
        "🎉 Terima kasih sudah tertarik bergabung menjadi <b>Member VIP</b>!\n"
        "Dengan bergabung, kamu akan mendapatkan <b>keuntungan eksklusif</b> yang tidak dimiliki anggota biasa.\n\n"
        "<b>Pilih tipe membership yang kamu inginkan:</b>\n"
        f"♾️ <b>VIP-Lifetime – <i>{LIFETIME_PRICE} Limited</i></b>\n"
        f"📅 <b>VIP-Monthly – <i>{MONTHLY_PRICE}/bulan</i></b>\n\n"
        "💡 <b>Catatan:</b>\n<i>Harga VIP-Lifetime akan terus naik seiring bertambahnya koleksi baru dan akan ditutup setelah kuota maksimal member terpenuhi (Limited).</i>\n\n"
        "🔑 <b>Keuntungan VIP:</b>\n"
        "<pre>"
        "• 👑 Badge Starlord 🥇\n"
        "• ✅ Akses Free ke semua koleksi\n"
        "• ⚡ Tidak butuh Key lagi\n"
        "• 🔒 Channel privat VIP khusus\n"
        "• ♾️ Prioritas terdepan\n"
        "</pre>"
        "🔥 Jangan lewatkan kesempatan ini, harga naik seiring koleksi bertambah!\n"
        "👉 Silakan pilih di bawah ⬇️"
    )


def _initial_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("♾️ Lifetime (Sekali Bayar)", callback_data="joinvip_lifetime")],
        [InlineKeyboardButton("📅 Monthly (Langganan Bulanan)", callback_data="joinvip_monthly")],
        [InlineKeyboardButton("❌ Batal", callback_data="joinvip_cancel")]
    ])

# ---------------- Handlers ------------------
@ app.on_message(filters.command("joinvip") & filters.private)
async def join_vip(client, message):
    """Step awal: kirim video/menu pilihan (Lifetime / Monthly)."""
    # beri XP seperti biasa
    await grant_xp_for_command(client, message, "joinvip")

    video_path = "Img/joinvip.mp4"  # sesuaikan
    sent = await message.reply_video(
        video=video_path,
        caption=_initial_caption(),
        parse_mode=ParseMode.HTML,
        reply_markup=_initial_keyboard()
    )

    # simpan message_id supaya bisa diedit nanti
    VIP_SESSIONS[message.from_user.id] = sent.id


# ---- pilih Lifetime ----
@ app.on_callback_query(filters.regex(r"^joinvip_lifetime$"))
async def cb_joinvip_lifetime(client, cq: CallbackQuery):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 Join via Trakteer", callback_data="joinvip_trakteer")],
        [InlineKeyboardButton("💳 Join via Saweria", callback_data="joinvip_saweria")],
        [InlineKeyboardButton("⬅️ Kembali", callback_data="joinvip_back")]
    ])

    teks = (
        "┏━━━━━━━━━━━━━━━━━\n"
        "┃ ♾️ <b>VIP LIFETIME</b>\n"
        "┗━━━━━━━━━━━━━━━━━\n\n"
        f"✅ Akses Premium SELAMANYA.\n"
        f"💰 Harga: <b>{LIFETIME_PRICE}</b> (sekali bayar).\n\n"
        "🔑 Pilih metode pembayaran di bawah:"
    )

    try:
        # jika pesan awal adalah media (video), edit_caption; jika gagal, fallback ke edit_text
        await cq.message.edit_caption(caption=teks, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except Exception:
        await cq.message.edit_text(text=teks, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    await cq.answer()


# ---- pilih Monthly ----
@ app.on_callback_query(filters.regex(r"^joinvip_monthly$"))
async def cb_joinvip_monthly(client, cq: CallbackQuery):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 Join via Trakteer", callback_data="joinvip_monthly_trakteer")],
        [InlineKeyboardButton("💳 Join via Saweria", callback_data="joinvip_monthly_saweria")],
        [InlineKeyboardButton("⬅️ Kembali", callback_data="joinvip_back")]
    ])

    teks = (
        "┏━━━━━━━━━━━━━━━━━\n"
        "┃ 📅 <b>VIP MONTHLY</b>\n"
        "┗━━━━━━━━━━━━━━━━━\n\n"
        f"✅ Akses Premium selama 30 hari.\n"
        f"💰 Harga: <b>{MONTHLY_PRICE}</b>/bulan.\n\n"
        "🔑 Pilih metode pembayaran di bawah:"
    )

    try:
        await cq.message.edit_caption(caption=teks, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except Exception:
        await cq.message.edit_text(text=teks, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    await cq.answer()


# ---- Back ke menu awal ----
@ app.on_callback_query(filters.regex(r"^joinvip_back$"))
async def cb_joinvip_back(client, cq: CallbackQuery):
    try:
        await cq.message.edit_caption(caption=_initial_caption(), parse_mode=ParseMode.HTML, reply_markup=_initial_keyboard())
    except Exception:
        await cq.message.edit_text(text=_initial_caption(), parse_mode=ParseMode.HTML, reply_markup=_initial_keyboard())
    await cq.answer()


# ---- Lifetime: Trakteer ----
@ app.on_callback_query(filters.regex(r"^joinvip_trakteer$"))
async def cb_joinvip_trakteer(client, cq: CallbackQuery):
    user = cq.from_user
    url_trakteer = "https://trakteer.id/BangsaBacolers/showcase"

    teks = (
        "┏━━━━━━━━━━━━━━━━━\n"
        "┃ 🔑 <b>JOIN VIP via Trakteer</b>\n"
        "┗━━━━━━━━━━━━━━━━━\n\n"
        "📌 Silakan lanjut ke link di bawah untuk menyelesaikan pembayaran:\n"
        f"➡️ <a href='{url_trakteer}'>Halaman Trakteer</a>\n\n"
        "💳 Harga sudah tercantum di sana.\n"
        "⚡ Akses VIP akan diberikan otomatis oleh sistem Trakteer setelah pembayaran berhasil."
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 Buka Trakteer", url=url_trakteer)],
        [InlineKeyboardButton("❌ Tidak Jadi", callback_data="joinvip_cancel")]
    ])

    try:
        await cq.message.edit_caption(caption=teks, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except Exception:
        await cq.message.edit_text(text=teks, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    # log ke channel (tambahkan keterangan Lifetime)
    now = datetime.now(JAKARTA_TZ).strftime("%d-%m-%Y %H:%M:%S")
    log_text = (
        "🔓 <b>JOIN VIP</b>\n"
        "━━━━━━━━━━━━\n"
        f"👤 Dari   : {user.mention}\n"
        f"🆔 ID     : <code>{user.id}</code>\n"
        f"📦 Metode : Trakteer (Lifetime)\n"
        f"🕒 Waktu  : {now}\n\n"
        f"⚠️ <b>Silahkan {format_admin_mentions()} konfirmasi!</b>"
    )
    await client.send_message(LOG_CHANNEL, log_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    await cq.answer()


# ---- Lifetime: Saweria ----
@ app.on_callback_query(filters.regex(r"^joinvip_saweria$"))
async def cb_joinvip_saweria(client, cq: CallbackQuery):
    user = cq.from_user
    url_saweria = "https://saweria.co/BangsaBacol"

    teks = (
        "┏━━━━━━━━━━━━━━━\n"
        "┃ 💳 <b>JOIN VIP via Saweria</b>\n"
        "┗━━━━━━━━━━━━━━━\n\n"
        "📌 <b>Ikuti langkah ini:</b>\n"
        "<pre>"
        f"1. Masuk ke <a href='{url_saweria}'>Saweria</a>\n"
        "2. Masukkan <b>ID Telegram</b> kamu di kolom pesan\n"
        f"3. Lakukan pembayaran total <b>{LIFETIME_PRICE}</b>\n"
        "4. Tulis pesan <i>JOIN VIP</i> saat transfer\n"
        "5. Screenshot bukti pembayaran buat jaga-jaga.\n"
        "</pre>"
        f"🆔 <b>ID Telegram:</b> <code>{user.id}</code>\n\n"
        "👑 <b>VIP jalur saweria harus invite manual, jadi mohon bersabar!</b>\n"
        "⚠️ <i>Jika kamu belum masuk Room VIP <b>1x24jam</b> langsung lapor ke Admin-Pusat!</i>"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Buka Saweria", url=url_saweria)],
        [InlineKeyboardButton("❌ Batal", callback_data="joinvip_cancel")]
    ])

    try:
        await cq.message.edit_caption(caption=teks, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except Exception:
        await cq.message.edit_text(text=teks, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    # log ke channel (tambahkan keterangan Lifetime)
    now = datetime.now(JAKARTA_TZ).strftime("%d-%m-%Y %H:%M:%S")
    log_text = (
        "🔓 <b>JOIN VIP</b>\n"
        "━━━━━━━━━━━━\n"
        f"👤 Dari   : {user.mention}\n"
        f"🆔 ID     : <code>{user.id}</code>\n"
        f"📦 Metode : Saweria (Lifetime)\n"
        f"🕒 Waktu  : {now}\n\n"
        f"⚠️ <b>Silahkan {format_admin_mentions()} konfirmasi!</b>"
    )
    await client.send_message(LOG_CHANNEL, log_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    await cq.answer()


# ---- Monthly: Trakteer ----
@ app.on_callback_query(filters.regex(r"^joinvip_monthly_trakteer$"))
async def cb_joinvip_monthly_trakteer(client, cq: CallbackQuery):
    user = cq.from_user
    url_trakteer = "https://trakteer.id/BangsaBacolers/showcase"

    teks = (
        "┏━━━━━━━━━━━━━━━━━\n"
        "┃ 🔑 <b>JOIN VIP via Trakteer</b>\n"
        "┗━━━━━━━━━━━━━━━━━\n\n"
        "📌 Silakan lanjut ke link di bawah untuk menyelesaikan pembayaran:\n"
        f"➡️ <a href='{url_trakteer}'>Halaman Trakteer</a>\n\n"
        f"💳 Harga: <b>{MONTHLY_PRICE}</b>/bulan.\n"
        "⚡ Akses VIP akan diberikan otomatis oleh sistem Trakteer setelah pembayaran berhasil."
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔑 Buka Trakteer", url=url_trakteer)],
        [InlineKeyboardButton("❌ Tidak Jadi", callback_data="joinvip_cancel")]
    ])

    try:
        await cq.message.edit_caption(caption=teks, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except Exception:
        await cq.message.edit_text(text=teks, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    # log ke channel (Monthly)
    now = datetime.now(JAKARTA_TZ).strftime("%d-%m-%Y %H:%M:%S")
    log_text = (
        "🔓 <b>JOIN VIP</b>\n"
        "━━━━━━━━━━━━\n"
        f"👤 Dari   : {user.mention}\n"
        f"🆔 ID     : <code>{user.id}</code>\n"
        f"📦 Metode : Trakteer (Monthly)\n"
        f"🕒 Waktu  : {now}\n\n"
        f"⚠️ <b>Silahkan {format_admin_mentions()} konfirmasi!</b>"
    )
    await client.send_message(LOG_CHANNEL, log_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    await cq.answer()


# ---- Monthly: Saweria ----
@ app.on_callback_query(filters.regex(r"^joinvip_monthly_saweria$"))
async def cb_joinvip_monthly_saweria(client, cq: CallbackQuery):
    user = cq.from_user
    url_saweria = "https://saweria.co/BangsaBacol"

    teks = (
        "┏━━━━━━━━━━━━━━━\n"
        "┃ 💳 <b>JOIN VIP via Saweria</b>\n"
        "┗━━━━━━━━━━━━━━━\n\n"
        "📌 <b>Ikuti langkah ini:</b>\n"
        "<pre>"
        f"1. Masuk ke <a href='{url_saweria}'>Saweria</a>\n"
        "2. Masukkan <b>ID Telegram</b> kamu di kolom pesan\n"
        f"3. Lakukan pembayaran total <b>{MONTHLY_PRICE}</b>\n"
        "4. Tulis pesan <i>JOIN VIP MONTHLY</i> saat transfer\n"
        "5. Screenshot bukti pembayaran buat jaga-jaga.\n"
        "</pre>"
        f"🆔 <b>ID Telegram:</b> <code>{user.id}</code>\n\n"
        "👑 <b>VIP jalur saweria harus invite manual, jadi mohon bersabar!</b>\n"
        "⚠️ <i>Jika kamu belum masuk Room VIP <b>1x24jam</b> langsung lapor ke Admin-Pusat!</i>"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💳 Buka Saweria", url=url_saweria)],
        [InlineKeyboardButton("❌ Batal", callback_data="joinvip_cancel")]
    ])

    try:
        await cq.message.edit_caption(caption=teks, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except Exception:
        await cq.message.edit_text(text=teks, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    # log ke channel (Monthly)
    now = datetime.now(JAKARTA_TZ).strftime("%d-%m-%Y %H:%M:%S")
    log_text = (
        "🔓 <b>JOIN VIP</b>\n"
        "━━━━━━━━━━━━\n"
        f"👤 Dari   : {user.mention}\n"
        f"🆔 ID     : <code>{user.id}</code>\n"
        f"📦 Metode : Saweria (Monthly)\n"
        f"🕒 Waktu  : {now}\n\n"
        f"⚠️ <b>Silahkan {format_admin_mentions()} konfirmasi!</b>"
    )
    await client.send_message(LOG_CHANNEL, log_text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    await cq.answer()


# ---- Cancel (hapus session + hapus pesan jika bisa) ----
@ app.on_callback_query(filters.regex(r"^joinvip_cancel_delete$"))
async def cb_joinvip_cancel_delete(client, cq: CallbackQuery):
    user = cq.from_user
    VIP_SESSIONS.pop(user.id, None)
    try:
        await cq.message.delete()
    except Exception:
        await cq.message.reply_text("🛑 <b>Okey, See u later!.</b>", parse_mode=ParseMode.HTML)
    await cq.answer("Dibatalkan & pesan dihapus ✅")


@ app.on_callback_query(filters.regex(r"^joinvip_cancel$"))
async def cb_joinvip_cancel(client, cq: CallbackQuery):
    user = cq.from_user
    VIP_SESSIONS.pop(user.id, None)

    teks = (
        "┏━━━━━━━━━━━━━━━\n"
        "┃ 🛑 <b>JOIN VIP DIBATALKAN</b>\n"
        "┗━━━━━━━━━━━━━━━\n\n"
        "❌ <b>Kamu membatalkan proses join VIP!</b>\n"
        "💡 <b>Ingat:</b> Harga VIP akan terus naik seiring bertambahnya koleksi baru.\n\n"
        "👉 Mau lihat <b>daftar koleksi</b> dulu sebelum memutuskan?"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📜 Mau Lihat Koleksi Lain", callback_data="verify_listvip")],
        [InlineKeyboardButton("❌ Lain Kali Aja", callback_data="joinvip_cancel_delete")]
    ])

    try:
        await cq.message.edit_caption(caption=teks, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except Exception:
        await cq.message.reply_text(text=teks, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    await cq.answer("Dibatalkan.")

# --------------- END ------------------
# Tips:
# - Sesuaikan LIFETIME_PRICE / MONTHLY_PRICE di atas.
# - Sesuaikan URL Trakteer/Saweria jika perlu.
# - Untuk proses verifikasi / pemberian VIP otomatis, sambungkan webhook atau job yang memeriksa notifikasi Trakteer/Saweria dan panggil fungsi pemberian akses (lifetime vs. expiry 30 hari).


# ================================
# QRIS FLOW
# ================================
QRIS_SESSIONS = {}

@app.on_message(filters.command("qris") & filters.private)
@require_membership(callback_data="verify_qris")
async def qris_topup(client, message):
    await grant_xp_for_command(client, message, "qris")
    user_id = message.from_user.id

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔑 TOP UP SEKARANG", callback_data="qris_start")]]
    )

    teks = (
        "┏━━━━━━━━━━━━━━━\n"
        "┃ 💳 <b>CARA TOP UP KEY</b>\n"
        "┗━━━━━━━━━━━━━━━\n\n"
        "<b>KALAU INI TOP UP PERTAMAMU:</b>\n"
        "<a href='https://t.me/BangsaBacol/182'>🎁 <b>PROMO STARTER KEY</b> 🎁</a>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "📌 <b>Rate:</b> Rp5.000 = 1 KEY\n"
        "📌 <b>Minimal:</b> 2 KEY (Rp10.000)\n\n"
        "🔑 <b>Langkah-langkah:</b>\n"
        "1️⃣ Klik tombol <b>TOP UP SEKARANG</b>\n"
        "2️⃣ Masukkan jumlah Key yang ingin dibeli\n"
        "3️⃣ Lanjutkan pembayaran via <b>Saweria/QRIS</b>\n\n"
        f"🆔 <b>ID Telegram kamu:</b> <code>{user_id}</code>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "✨ <i>Proses cepat & otomatis. Pastikan ID Telegram ditulis dengan benar!</i>"
    )

    image_path = "Img/topup.jpg"

    await message.reply_photo(
        photo=image_path,
        caption=teks,
        parse_mode=ParseMode.HTML,
        reply_markup=keyboard
    )

@app.on_callback_query(filters.regex("^qris_start$"))
async def cb_qris_start(client, cq: CallbackQuery):
    user_id = cq.from_user.id
    # simpan session dengan referensi pesan agar bisa di-edit nanti
    QRIS_SESSIONS[user_id] = {"chat_id": cq.message.chat.id, "msg_id": cq.message.id}

    teks = (
        "┏━━━━━━━━━━━━━━━\n"
        "┃ 💡 <b>INPUT JUMLAH KEY</b>\n"
        "┗━━━━━━━━━━━━━━━\n\n"
        "📌 <b>Rate:</b> 1 KEY = Rp5.000\n"
        "➡️ <i>Contoh:</i> <code>5</code>\n\n"
        "✍️ Silakan ketik jumlah KEY yang ingin kamu beli:"
    )

    # tambahkan tombol batal agar user bisa cancel
    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ BATAL", callback_data="qris_cancel")]]
    )

    try:
        await cq.message.edit_caption(
            caption=teks,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
    except Exception:
        # kalau edit gagal, fallback kirim reply (tetap simpan session)
        await cq.message.reply_text(teks, parse_mode=ParseMode.HTML)
    await cq.answer()

async def qris_session_handler(client, message):
    user_id, username, fullname = get_user_identity(message.from_user)

    sess = QRIS_SESSIONS.get(user_id)
    if not sess:
        return  # tidak ada sesi aktif

    text = (message.text or "").strip()

    # validasi input angka
    try:
        jumlah_key = int(text)
    except (ValueError, TypeError):
        await message.reply(
            "⚠️ Input tidak valid. Masukkan angka saja (contoh: <code>5</code>).",
            parse_mode=ParseMode.HTML
        )
        return

    if jumlah_key < 2:
        await message.reply(
            "⚠️ Minimal pembelian adalah <b>2 KEY</b> (Rp10.000).",
            parse_mode=ParseMode.HTML
        )
        return

    # ✅ input valid
    total_idr = jumlah_key * 5000
    url_topup = "https://saweria.co/BangsaBacol"
    now = datetime.now(JAKARTA_TZ).strftime("%d-%m-%Y %H:%M:%S")

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💳 BAYAR SEKARANG", url=url_topup)],
            [InlineKeyboardButton("❌ BATAL", callback_data="qris_cancel")]
        ]
    )

    teks = (
        "┏━━━━━━━━━━━━━━━━━━━\n"
        "┃ ✅ <b>DETAIL TOP UP</b>\n"
        "┗━━━━━━━━━━━━━━━━━━━\n"
        "<pre>"
        f"User    : {fullname} ({username})\n"
        f"ID      : {user_id}\n"
        f"Waktu   : {now}\n"
        "─────────────────────\n"
        f"Jumlah  : {jumlah_key} KEY\n"
        f"Total   : Rp{total_idr:,}\n"
        "</pre>\n"
        "⚠️ <i>Jika Key belum masuk dalam <b>1x24 jam</b>, segera lapor Admin-Pusat!</i>\n\n"
        "📌 Klik tombol <b>BAYAR SEKARANG</b> untuk melanjutkan."
    )

    chat_id = sess.get("chat_id")
    msg_id = sess.get("msg_id")

    try:
        await client.edit_message_caption(
            chat_id,
            msg_id,
            caption=teks,
            parse_mode=ParseMode.HTML,
            reply_markup=keyboard
        )
    except Exception:
        # kalau gagal edit (misalnya pesan sudah dihapus), fallback kirim baru
        await message.reply(teks, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    # selesai → hapus sesi
    QRIS_SESSIONS.pop(user_id, None)

    # kirim log ke channel admin
    try:
        LOG_CHANNEL = -1002316200587  # sesuaikan
        now = datetime.now(JAKARTA_TZ).strftime("%d-%m-%Y %H:%M:%S")
        await client.send_message(
            LOG_CHANNEL,
            "🔑 <b>TOP UP KEY</b>\n"
            "━━━━━━━━━━━━━━━\n"
            f"👤 User  : {fullname} ({username})\n"
            f"🆔 ID: <code>{user_id}</code>\n"
            f"🔑 Jumlah: <b>{jumlah_key} KEY</b>\n"
            f"💰 Total: <b>Rp{total_idr:,}</b>\n"
            f"🕒 Waktu  : {now}\n\n"
            f"⚠️ <b>Silahkan {format_admin_mentions()} konfirmasi!</b>",
            parse_mode=ParseMode.HTML
        )
    except Exception:
        logger.exception("Gagal kirim log topup")

@app.on_callback_query(filters.regex("^qris_cancel_delete$"))
async def cb_qris_cancel_delete(client, cq: CallbackQuery):
    """Batal topup QRIS + hapus pesan supaya flow bersih."""
    user_id = cq.from_user.id
    QRIS_SESSIONS.pop(user_id, None)  # ✅ Bersihkan session di awal

    try:
        await cq.message.delete()
    except Exception as e:
        # fallback kalau pesan sudah tidak bisa dihapus
        await cq.message.reply_text(
            "🛑 <b>Okey sampai jumpa!</b>",
            parse_mode=ParseMode.HTML
        )
    await cq.answer("Topup dibatalkan & pesan dihapus ✅")


@app.on_callback_query(filters.regex("^qris_cancel$"))
async def cb_qris_cancel(client, cq: CallbackQuery):
    user_id = cq.from_user.id
    QRIS_SESSIONS.pop(user_id, None)  # ✅ Bersihkan session di awal

    teks = (
        "┏━━━━━━━━━━━━━━━━━━━━\n"
        "┃ 🛑 <b>TOPUP DIBATALKAN</b>\n"
        "┗━━━━━━━━━━━━━━━━━━━━\n\n"
        "❌ Proses topup telah dibatalkan.\n\n"
        "💡 Tenang saja, kalau sudah gajian nanti kamu bisa mencoba lagi lewat perintah <code>/qris</code>.\n\n"
        "👉 Mau lihat <b>daftar koleksi</b> dulu sambil menunggu?"
    )
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📜 Lihat Koleksi", callback_data="verify_listvip")],
        [InlineKeyboardButton("❌ Lain Kali Aja", callback_data="qris_cancel_delete")]
    ])

    try:
        await cq.message.edit_caption(caption=teks, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    except Exception:
        # fallback kalau pesan sudah tidak bisa diedit
        await cq.message.reply_text(teks, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    await cq.answer("Topup dibatalkan.")


# --- Leaderboard Komunitas ---
USER_ACTIVITY_FILE = Path("data/user_activity.json")

def load_user_activity():
    if USER_ACTIVITY_FILE.exists():
        with open(USER_ACTIVITY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

def save_user_activity(data):
    USER_ACTIVITY_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(USER_ACTIVITY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def log_user_activity(user_id, username):
    data = load_user_activity()
    user = str(user_id)
    if user not in data:
        data[user] = {"username": username, "count": 0}
    data[user]["count"] += 1
    data[user]["username"] = username  # update username jika berubah
    save_user_activity(data)

@app.on_message(filters.command("top"))
async def top_users_command(client, message):
    data = load_user_activity()
    if not data:
        await message.reply("📊 Belum ada data aktivitas user.")
        return

    # Urutkan berdasarkan count
    top = sorted(data.items(), key=lambda x: x[1].get("count", 0), reverse=True)[:10]
    lines = ["🏆 <b>Top Bacolers</b> (paling aktif):\n"]
    for i, (uid, info) in enumerate(top, 1):
        uname = f"@{info.get('username')}" if info.get('username') else f"ID:{uid}"
        count = info.get("count", 0)
        lines.append(f"{i}. {uname} — {count} akses")

    await message.reply("\n".join(lines), parse_mode=ParseMode.HTML)


@app.on_message(filters.command("reset_top") & filters.user(OWNER_IDS))
async def reset_top_command(client, message):
    save_user_activity({})
    await message.reply("✅ Data leaderboard direset.")

# ================================
# Lapor System (/lapor)
# ================================
waiting_lapor_users: set[int] = set()
waiting_feedback_users: set[int] = set()
last_feedback_time: dict[int, datetime] = {}
last_lapor_time: dict[int, datetime] = {}
LAPOR_COOLDOWN = timedelta(minutes=1)

async def _broadcast_lapor(client: Client, text: str | None, user):
    mention = f'<a href="tg://user?id={user.id}">{user.first_name}</a>'
    username = f"@{user.username}" if user.username else "(no username)"
    waktu = datetime.now(JAKARTA_TZ).strftime("%Y-%m-%d %H:%M:%S")
    header = (
        "📩 <b>LAPORAN BARU</b>\n"
        f"• Dari: {mention} {username}\n"
        f"• User ID: {user.id}\n"
        f"• Waktu: {waktu}\n"
    )
    for admin_id in ALLOWED_IDS:
        try:
            await client.send_message(admin_id, header, parse_mode=ParseMode.HTML)
            if text:
                await client.send_message(admin_id, f"Pesan:\n{text}")
        except Exception as e:
            logger.error(f"Gagal kirim laporan ke {admin_id}: {e}")

@app.on_message(filters.command("lapor") & filters.private, group=10)
async def lapor_start(client: Client, message: Message):
    await grant_xp_for_command(client, message, "lapor")
    user_id = message.from_user.id
    now = datetime.now(JAKARTA_TZ)

    last_time = last_lapor_time.get(user_id)
    if last_time and now - last_time < LAPOR_COOLDOWN:
        remain = int((LAPOR_COOLDOWN - (now - last_time)).total_seconds())
        await message.reply(f"⏳ Tunggu {remain} detik sebelum mengirim laporan lagi.")
        return

    # Mode langsung: /lapor <teks>
    args = message.text.split(maxsplit=1)
    if len(args) > 1 and args[1].strip():
        laporan_text = args[1].strip()
        last_lapor_time[user_id] = now
        try:
            await _broadcast_lapor(client, laporan_text, message.from_user)
            await message.reply("✅ Terima kasih! Laporanmu sudah terkirim ke Admin.")
        except Exception as e:
            logger.error(f"Gagal kirim laporan langsung: {e}")
            await message.reply("❌ Gagal mengirim laporan.")
        return

    if user_id in waiting_lapor_users:
        await message.reply("📃️ Kamu masih dalam mode laporan. Kirim pesan/mediamu sekarang atau /batal untuk batal.")
        return

    waiting_lapor_users.add(user_id)
    await message.reply(
        "┏━━━━━━━━━━━━━━━━━━━\n"
        "┃ 👋 **LAPORAN USER**\n"
        "┗━━━━━━━━━━━━━━━━━━━\n\n"
        "✍️ Silahkan kirim **teks atau media** (Foto, Video, Voice, Dokumen).\n"
        "❌ Kalau berubah pikiran, ketik **/batal** untuk membatalkan.\n\n"
        "📃 **Tips:**\n"
        "• Tuliskan semua laporanmu dalam satu kali kirim.\n"
        "• Admin Pusat bisa langsung membacanya dengan jelas.\n\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "Terima kasih atas laporannya! 🙏",
        parse_mode=ParseMode.MARKDOWN
    )

@app.on_message(filters.command("batal") & filters.private, group=10)
async def lapor_cancel(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id in waiting_lapor_users:
        waiting_lapor_users.discard(user_id)
        await message.reply("✅ Mode laporan dibatalkan.")
    else:
        await message.reply("ℹ️ Kamu tidak sedang dalam mode laporan.")

@app.on_message(filters.private & ~filters.regex(r"^/"), group=11)
async def lapor_receive(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id not in waiting_lapor_users:
        return

    try:
        mention = f'<a href="tg://user?id={message.from_user.id}">{message.from_user.first_name}</a>'
        username = f"@{message.from_user.username}" if message.from_user.username else "(no username)"
        waktu = datetime.now(JAKARTA_TZ).strftime("%Y-%m-%d %H:%M:%S")

        header = (
            "📩 <b>LAPORAN BARU</b>\n"
            f"• Dari: {mention} {username}\n"
            f"• User ID: {user_id}\n"
            f"• Waktu: {waktu}\n"
        )

        for admin_id in ALLOWED_IDS:
            try:
                await client.send_message(admin_id, header, parse_mode=ParseMode.HTML)
                if message.media:
                    await client.copy_message(admin_id, message.chat.id, message.id)
                elif (message.text or "").strip():
                    await client.send_message(admin_id, f"Pesan:\n{message.text}")
                else:
                    await client.send_message(admin_id, "📃️ (Pesan kosong/tidak didukung)")
            except Exception as e:
                logger.error(f"Gagal kirim laporan ke {admin_id}: {e}")

        await message.reply("✅ Laporanmu sudah diteruskan ke Admin.")

    except Exception as e:
        logger.error(f"Gagal terima laporan: {e}")
        await message.reply("❌ Gagal mengirim laporan.")
    finally:
        waiting_lapor_users.discard(user_id)
        last_lapor_time[user_id] = datetime.now(JAKARTA_TZ)
        message.stop_propagation()

# ===============================

# Command request
@app.on_message(filters.private & filters.command("request") & filters.private)
@require_membership(callback_data="verify_request")
async def request_cmd(client, message):
    await grant_xp_for_command(client, message, "request")
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🇮🇩 Lokal", callback_data="vote_lokal")],
        [InlineKeyboardButton("🇨🇳 Chindo", callback_data="vote_chindo")],
        [InlineKeyboardButton("🌍 Bule", callback_data="vote_bule")]
    ])
    await message.reply("📊 Silakan pilih untuk hari ini 👇", reply_markup=keyboard)

# Callback vote
@app.on_callback_query(filters.regex(r"^vote_"))
async def handle_vote(client, callback_query: CallbackQuery):
    user_id = str(callback_query.from_user.id)
    today = datetime.now().date().isoformat()
    votes = load_votes()

    # Cek user sudah vote belum
    if user_id in votes and votes[user_id]["date"] == today:
        await callback_query.answer("⚠️ Kamu sudah vote hari ini!", show_alert=True)
        return

    # Mapping pilihan
    mapping = {
        "vote_lokal": "🇮🇩 Lokal",
        "vote_chindo": "🇨🇳 Chindo",
        "vote_bule": "🌍 Bule"
    }

    choice = mapping.get(callback_query.data, "❓ Tidak diketahui")

    # Simpan vote
    votes[user_id] = {
        "date": today,
        "choice": choice
    }
    save_votes(votes)

    await callback_query.answer(f"✅ Pilihanmu: {choice} tersimpan!", show_alert=True)

    # 🚩 Log publik anonim
    data = load_user_data()
    user = data.get(user_id, {"badge": "Stranger 🔰"})
    try:
        await send_public_log(client, "vote", badge=user.get("badge"), extra=choice)
    except Exception as e:
        logger.error(f"Public log vote gagal: {e}")

# Hasil rekap (khusus admin)
@app.on_message(filters.command("hasil_request") & filters.user([7112438057]))  # ganti ID admin
async def hasil_request(client, message):
    today = datetime.now().date().isoformat()
    votes = load_votes()

    lokal = sum(1 for v in votes.values() if v["date"] == today and v["choice"] == "🇮🇩 Lokal")
    chindo = sum(1 for v in votes.values() if v["date"] == today and v["choice"] == "🇨🇳 Chindo")
    bule = sum(1 for v in votes.values() if v["date"] == today and v["choice"] == "🌍 Bule")

    await message.reply(
        f"📊 Rekap hari ini ({today}):\n\n"
        f"🇮🇩 Lokal: {lokal}\n"
        f"🇨🇳 Chindo: {chindo}\n"
        f"🌍 Bule: {bule}"
    )

@app.on_message(filters.command("stats"))
async def stats_command(client, message):
    if not is_owner(message):  # <-
        await message.reply("❌ Perintah ini hanya untuk Admin-Pusat ya!")
        return
    try:
        period_days = 7
        stats = parse_clicks_log_json(days_back=period_days)
        if stats["status"] in ("no_log_file", "read_error", "no_recent_clicks"):
            text = (
                f"📈 Statistik ({period_days} hari)\n\n"
                f"🔢 Total klik: {stats.get('total_clicks', 0)}\n"
                f"👥 Pengguna unik: {stats.get('unique_users', 0)}\n\n"
                f"ℹ️ {stats.get('message', 'Belum ada data.')}"
            )
            if message.from_user and message.from_user.id == OWNER_IDS:
                log = _check_log_file_status()
                text += (
                    f"\n\n🔧 Debug (Admin)\n"
                    f"• File log: {'✅' if log['exists'] else '❌'}\n"
                    f"• Baris: {log.get('lines', 0)}\n"
                    f"• Ukuran: {log.get('size', 0)} B\n"
                )
            await message.reply(text, parse_mode=ParseMode.MARKDOWN); return

        items = sorted(stats.get("by_code", {}).items(), key=lambda x: x[1], reverse=True)[:5]
        lines = [
            f"📈 Statistik ({period_days} hari)",
            f"🔢 Total klik: {stats['total_clicks']}",
            f"👥 Pengguna unik: {stats['unique_users']}",
            "",
            "🏆 Top 5 Kode:" if items else "Tidak ada data kode untuk periode ini."
        ]
        for i, (code, count) in enumerate(items, 1):
            lines.append(f"{i}. {code} — {count}")
        await message.reply("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Error di /stats: {e}")
        await message.reply("❌ Terjadi kesalahan saat menghasilkan statistik.")

@app.on_message(filters.command("log"))
async def log_command(client, message):
    """OWNER: tampilkan 20 baris terakhir klik human log."""
    if not is_owner(message):
        await message.reply("❌ Apa sih?! Perintah ini hanya untuk OWNER."); return
    if not CLICKS_HUMAN.exists():
        await message.reply("📭 Belum ada log akses tercatat."); return
    try:
        with open(CLICKS_HUMAN, "r", encoding="utf-8") as f:
            lines = f.readlines()
        last = lines[-20:] if len(lines) > 20 else lines
        text = "".join(last)
        if len(text) > 3500:
            text = "... (dipotong)\n" + text[-3500:]
        await message.reply(f"<b>📜 20 Log Akses Terakhir</b>\n\n<pre>{text}</pre>", parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Gagal membaca clicks_human.log: {e}")
        await message.reply("❌ Gagal membaca file log.")

@app.on_message(filters.command("dashboard"))
async def dashboard_command(client, message):
    if not is_owner(message):  # <-
        await message.reply("❌ Gak usah kepo! Perintah ini hanya untuk Admin-Pusat.")
        return
    try:
        period_days = 7
        text = build_dashboard_text(period_days)
        kb = build_dashboard_keyboard(period_days)
        await message.reply(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"Error di /dashboard: {e}")
        await message.reply("❌ Error memuat dashboard.")

@app.on_callback_query(filters.regex(r"^dashboard:\d+$"))
async def dashboard_cb_period(client, cq: CallbackQuery):
    try:
        period_days = int(cq.data.split(":")[1])
        text = build_dashboard_text(period_days)
        kb = build_dashboard_keyboard(period_days)
        try:
            await cq.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        except MessageNotModified:
            pass
        await cq.answer()
    except Exception as e:
        logger.error(f"Error dashboard callback: {e}")
        await cq.answer("❌ Gagal memperbarui dashboard.", show_alert=False)

@app.on_message(filters.command("reload_interaction") & filters.user(OWNER_IDS))
async def reload_interaction_cmd(client, message):
    try:
        load_interaction_config()
        await message.reply(f"✅ Reload Interaction OK. ({len(INTERACTION_MESSAGES)} pesan, interval {INTERACTION_INTERVAL_MINUTES}m)")
    except Exception:
        await message.reply("❌ Gagal reload interaction config. Cek log.")

def build_list_keyboard(page_codes: list[str], page: int, pages: int) -> InlineKeyboardMarkup:
    kb = []

    # 📁 Tambahkan emoji ke setiap nama kode
    for code in page_codes:
        display_name = f"📁 {code}"
        kb.append([
            InlineKeyboardButton(
                display_name,
                callback_data=f"list_show|{code}|{page}"
            )
        ])

    # 🔄 Tombol navigasi halaman
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton("⏮️", callback_data="list|1"))

    if page > 10:
        nav.append(InlineKeyboardButton("⏪ -10", callback_data=f"list|{max(1, page-10)}"))

    if page > 1:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"list|{page-1}"))

    if page < pages:
        nav.append(InlineKeyboardButton("Next ➡️", callback_data=f"list|{page+1}"))

    if page <= pages - 10:
        nav.append(InlineKeyboardButton("+10 ⏩", callback_data=f"list|{min(pages, page+10)}"))

    if page < pages:
        nav.append(InlineKeyboardButton("⏭️", callback_data=f"list|{pages}"))

    if nav:
        kb.append(nav)

    # ⚙️ Tombol refresh + close
    kb.append([
        InlineKeyboardButton("🔄 Refresh", callback_data=f"list|{page}"),
        InlineKeyboardButton("❌ Tutup", callback_data="list_close")
    ])

    return InlineKeyboardMarkup(kb)

@app.on_message(filters.command("free") & filters.private)
@require_membership(callback_data="verify_free")
async def list_command(client, message):
    await grant_xp_for_command(client, message, "free")
    user_id = message.from_user.id
    username = message.from_user.username or ""
    log_user_activity(user_id, username)

    # --- Gabungan cek akses ---
    if not (is_owner(message) or is_admin(message) or has_shimmer_or_higher(user_id)):
        teks = (
            "┏━━━━━━━━━━━━━━━━━━━━\n"
            "┃ ❌ <b>AKSES DITOLAK</b>\n"
            "┗━━━━━━━━━━━━━━━━━━━━\n\n"
            "🚫 Fitur ini hanya tersedia untuk pengguna dengan <b>badge tingkat lanjut</b>:\n"
            "🥉 <b>Shimmer</b>\n"
            "🥈 <b>Stellar</b>\n"
            "🥇 <b>Starlord</b>\n\n"
            "👉 Cara mendapatkannya:\n"
            "1️⃣ Gunakan <code>/profile</code> untuk cek XP & badge kamu.\n"
            "2️⃣ Kumpulkan XP setiap hari dengan memakai perintah bot.\n"
            "3️⃣ Naikkan level badge-mu sampai minimal <b>Shimmer 🥉</b>.\n\n"
            "✨ Setelah badge cukup, kamu otomatis bisa membuka fitur ini."
        )
        await message.reply(teks, parse_mode=ParseMode.HTML)
        return
    # --------------------------

    codes = sorted(list(STREAM_MAP.keys()))
    if not codes:
        await message.reply("📭 Daftar koleksi kosong.")
        return

    page_codes, page, pages, total = paginate_codes(codes, 1)

    txt = (
        "┏━━━━━━━━━━━━━━━\n"
        "┃ 📜 <b>Daftar Koleksi</b>\n"
        "┗━━━━━━━━━━━━━━━\n\n"
        f"📑 Halaman {page} dari {pages}\n"
        "Pilih salah satu kode di bawah untuk melihat detail lengkapnya."
    )

    await message.reply(
        txt,
        parse_mode=ParseMode.HTML,
        reply_markup=build_list_keyboard(page_codes, page, pages)
    )

# --- Callback Query Handlers ---
@app.on_callback_query(filters.regex(r"^(verify|list|list_show|list_close).*"))
async def handle_callback(client: Client, cq: CallbackQuery):
    logger.info(f"[CALLBACK] data={cq.data} from={cq.from_user.id}")
    data = cq.data
    user_id = cq.from_user.id

    # 🔐 Akses cek (opsional kalau mau filter khusus free)
    if data.startswith("free"):
        if not (is_owner(cq) or is_admin(cq) or has_stellar_or_higher(user_id)):
            await cq.answer("Perintah ini hanya untuk Owner/Admin/Stellar+.", show_alert=True)
            return

    # 📑 Pagination
    if data.startswith("list|"):
        try:
            _, page_str = data.split("|", 1)
            page = int(page_str)
        except Exception:
            await cq.answer("❌ Data tidak valid.", show_alert=True)
            return

        codes = sorted(list(STREAM_MAP.keys()))
        page_codes, page, pages, total = paginate_codes(codes, page)

        txt = (
            "┏━━━━━━━━━━━━━━━\n"
            "┃ 📜 <b>Daftar Koleksi</b>\n"
            "┗━━━━━━━━━━━━━━━\n\n"
            f"📑 Halaman {page} dari {pages}\n"
            "Pilih salah satu kode di bawah untuk melihat detail lengkapnya."
        )

        try:
            await cq.message.edit_text(
                txt,
                parse_mode=ParseMode.HTML,
                reply_markup=build_list_keyboard(page_codes, page, pages)
            )
        except MessageNotModified:
            await cq.answer("⏳ Sudah di halaman ini.")
        return

    # 💿 Detail koleksi
    if data.startswith("list_show|"):
        _, code, return_page = data.split("|", 2)
        # (isi detail koleksi yang sudah kamu buat)

    # ❌ Tutup menu
    if data == "list_close":
        try:
            await cq.message.delete()
        except Exception:
            await cq.answer("❌ Tidak bisa hapus pesan ini.", show_alert=True)
        return

    # ✅ Verifikasi join group & channel sebelum akses koleksi
    if data.startswith("list_show|"):
        try:
            _, code, return_page = data.split("|", 2)
        except Exception as e:
            logger.error(f"[list_show] split gagal: {e}, data={data}")
            await cq.answer("❌ Data tidak valid.", show_alert=True)
            return

        link, raw_thumb = get_stream_data(code)
        thumb = resolve_thumb(raw_thumb)
        if not link:
            logger.warning(f"[list_show] link tidak ditemukan untuk code={code}")
            await cq.answer("⚠️ Koleksi tidak ditemukan.", show_alert=True)
            return

        caption = (
        "┏━━━━━━━━━━━━━━━\n"
        f"┃ 💿 <b>Koleksi {code}</b>\n"
        "┗━━━━━━━━━━━━━━━\n"
        "✨ Klik tombol di bawah untuk menonton."
        )

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ Tonton Sekarang", url=link)],
            [InlineKeyboardButton("⬅️ Kembali", callback_data=f"list|{return_page}")],
            [InlineKeyboardButton("❌ Tutup", callback_data="list_close")],
        ])

        try:
            if thumb and os.path.exists(thumb):
                await cq.message.reply_photo(
                    photo=thumb,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb
                )
                try:
                    await cq.message.delete()
                except Exception:
                    pass
            else:
                await cq.message.edit_text(
                    caption,
                    parse_mode=ParseMode.HTML,
                    reply_markup=kb
                )
        except MessageNotModified:
            await cq.answer("⏳ Sudah di halaman ini.")
        except Exception as e:
            logger.error(f"[list_show] error: {e}")
            await cq.answer("⚠️ Gagal memuat detail.", show_alert=True)

# --- Global Uptime ---
BOT_START_TIME = datetime.now(JAKARTA_TZ)

# --- Command: healthcheck ---
@app.on_message(filters.command("healthcheck") & filters.private)
async def healthcheck_cmd(client, message):
    user = message.from_user
    if user.id not in OWNER_IDS:
        await message.reply_text("❌ Kepo amat! Hanya owner yang dapat menggunakan command ini.")
        return

    try:
        uptime = datetime.now(JAKARTA_TZ) - BOT_START_TIME
        days, remainder = divmod(uptime.total_seconds(), 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)

        # --- memory usage (safe fallback) ---
        mem_info = "N/A"
        try:
            if psutil:
                mem_mb = round(psutil.Process().memory_info().rss / 1024 / 1024, 1)
                mem_info = f"{mem_mb} MB"
            else:
                # fallback for Unix-like systems using resource (may vary by platform)
                try:
                    import resource
                    ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
                    # ru_maxrss is in kilobytes on Linux, bytes on macOS — normalize heuristically
                    if ru > 10**6:
                        # likely bytes
                        mem_mb = round(ru / 1024 / 1024, 1)
                    else:
                        # likely kilobytes
                        mem_mb = round(ru / 1024, 1)
                    mem_info = f"{mem_mb} MB"
                except Exception:
                    mem_info = "N/A"
        except Exception:
            mem_info = "N/A"

        teks = (
            "📊 <b>BOT HEALTHCHECK</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"🟢 Status       : <b>ONLINE</b>\n"
            f"🕒 Uptime       : {int(days)}d {int(hours)}h {int(minutes)}m {int(seconds)}s\n"
            f"📦 VIP Sessions : {len(VIP_SESSIONS)} aktif\n"
            f"📦 QRIS Sessions: {len(QRIS_SESSIONS)} aktif\n"
            f"📦 FreeKey Sess : {len(FREEKEY_SESSIONS) if 'FREEKEY_SESSIONS' in globals() else 0} aktif\n"
            f"💾 Memory       : {mem_info}\n"
        )

        await message.reply_text(teks, parse_mode=ParseMode.HTML)

    except Exception as e:
        await message.reply_text(f"❌ Gagal healthcheck: <code>{str(e)}</code>", parse_mode=ParseMode.HTML)

# Extra: prune logs command (owner)
@app.on_message(filters.command("prune_logs"))
async def prune_logs_cmd(client, message):
    if not is_owner(message):
        await message.reply("❌ Hadeh! Perintah ini hanya untuk OWNER."); return
    days = RETENTION_DAYS
    try:
        if len(message.command) > 1 and message.command[1].isdigit():
            days = max(1, int(message.command[1]))
    except Exception:
        pass
    prune_clicks_log(days)
    await message.reply(f"🧹 Log dikompak untuk {days} hari terakhir.")

# --- Admin-Only: manage links ---
@app.on_message(filters.command("add") & filters.private)
async def add_link_command(client, message):
    if not is_owner(message):
        await message.reply("❌ Ngapain?! Perintah ini hanya untuk owner.")
        logger.warning(f"Unauthorized access attempt to /add by user {message.from_user.id}")
        return
    try:
        # /add <kode> <link> [thumbnail]
        parts = message.text.split(maxsplit=3)
        if len(parts) < 3:
            raise ValueError("❌ Format tidak valid. Gunakan:\n`/add <kode> <link> [thumbnail_dengan_ekstensi]`")
        _, code, link, *rest = parts
        thumbnail = rest[0].strip() if rest else None
        if thumbnail and "." not in thumbnail:
            thumbnail += ".jpg"

        if code in STREAM_MAP:
            await message.reply(f"⚠️ Kode `{code}` sudah ada. Link akan diupdate.", parse_mode=ParseMode.MARKDOWN)

        STREAM_MAP[code] = {"link": link}
        if thumbnail:
            STREAM_MAP[code]["thumbnail"] = thumbnail
        save_stream_map()

        await message.reply(
            f"✅ Berhasil menambahkan/mengupdate kode `{code}`.\nLink: `{link}`\nThumbnail: `{thumbnail or 'Tidak ada'}`",
            parse_mode=ParseMode.MARKDOWN,
        )
        logger.info(f"Owner {message.from_user.id} menambahkan/mengupdate kode '{code}'")
    except Exception as e:
        logger.error(f"Invalid format for /add: {e}")
        await notify_owner(f"/add error: {e}")
        await message.reply(
            "❌ Format tidak valid. Gunakan:\n`/add <kode> <link> [nama_thumbnail_tanpa_ekstensi]`",
            parse_mode=ParseMode.MARKDOWN,
        )

@app.on_message(filters.command("delete") & filters.private)
async def delete_link_command(client, message):
    if not is_owner(message):
        await message.reply("❌ Kamu siapa? Perintah ini hanya untuk owner.")
        logger.warning(f"Unauthorized access attempt to /delete by user {message.from_user.id}")
        return
    try:
        parts = message.text.split()
        if len(parts) < 2:
            await message.reply("❌ Gunakan:\n`/delete <kode>`", parse_mode=ParseMode.MARKDOWN)
            return
        code = parts[1]
        if code not in STREAM_MAP:
            await message.reply(f"⚠️ Kode `{code}` tidak ditemukan.", parse_mode=ParseMode.MARKDOWN)
            return
        del STREAM_MAP[code]
        save_stream_map()
        await message.reply(f"🗑️ Berhasil menghapus kode `{code}`.", parse_mode=ParseMode.MARKDOWN)
        logger.info(f"Owner {message.from_user.id} menghapus kode '{code}'")
    except Exception as e:
        logger.error(f"Error /delete: {e}")
        await notify_owner(f"/delete error: {e}")
        await message.reply("❌ Terjadi kesalahan saat memproses perintah.")

# ============================================================
# 5) HANDLER UMUM (bisa diakses semua orang)
# ============================================================

def has_shimmer_or_higher(user_id: int) -> bool:
    """Cek apakah user minimal punya badge Shimmer 🥉 atau lebih tinggi."""
    data = load_user_data()
    info = data.get(str(user_id), {})
    xp = int(info.get("xp", 0))
    badge = _badge_for_xp(xp)
    return badge in ["Shimmer 🥉", "Stellar 🥈", "Starlord 🥇"]

def _badge_for_xp(xp: int) -> str:
    for name, threshold in BADGE_TIERS:
        if xp >= threshold:
            return name
    return "Stranger 🔰"

def _next_tier_info(xp: int):
    tiers = sorted(BADGE_TIERS, key=lambda t: t[1])
    for name, threshold in tiers:
        if xp < threshold:
            return name, threshold - xp
    return None, 0  # sudah max

def _progress_bar(xp: int) -> str:
    # progress menuju tier berikutnya
    next_name, remain = _next_tier_info(xp)
    if not next_name:
        return "▰▰▰▰▰ MAX"
    tiers = sorted([t[1] for t in BADGE_TIERS])
    # cari batas bawah & atas segment saat ini
    lower = max([t for t in tiers if t <= xp], default=0)
    upper_candidates = [t for t in tiers if t > xp]
    upper = min(upper_candidates) if upper_candidates else lower
    span = max(upper - lower, 1)
    filled = int(round(5 * (xp - lower) / span))
    filled = max(0, min(5, filled))
    return "▰" * filled + "▱" * (5 - filled)

#----------- SUPORTER

def get_supporter_since(user_id: int) -> int:
    data = load_user_data()
    return int(data.get(str(user_id), {}).get("supporter_since", 0))

def set_supporter_since(user_id: int, ts: int):
    data = load_user_data()
    user = data.get(str(user_id), {})
    user["supporter_since"] = ts
    data[str(user_id)] = user
    save_user_data(data)

async def update_supporter_badge(client, user_id: int):
    bio_text = ""
    try:
        chat = await client.get_chat(user_id)
        bio_text = getattr(chat, "bio", "") or ""
    except Exception:
        try:
            user_obj = await client.get_users(user_id)
            bio_text = getattr(user_obj, "bio", "") or ""
        except Exception:
            pass

    tokens = []
    if CHANNEL_USERNAME:
        tokens.append(CHANNEL_USERNAME.lstrip("@").lower())
    if EXTRA_CHANNEL:
        tokens.append(EXTRA_CHANNEL.lstrip("@").lower())
    if GROUP_USERNAME:
        tokens.append(GROUP_USERNAME.lstrip("@").lower())
    try:
        for bm in BOT_MIRRORS:
            if bm.get("username"):
                tokens.append(bm["username"].lower())
    except Exception:
        pass

    tokens.extend(["bangsabacol", "bacol", "bangsa bacol"])

    if bio_matches(bio_text, tokens):
        if not has_supporter_badge(user_id):  # baru pertama kali valid
            set_supporter_since(user_id, int(time.time()))
        set_supporter_badge(user_id, True)
    else:
        set_supporter_badge(user_id, False)
        set_supporter_since(user_id, 0)

# --- Command /profile ---
@app.on_message(filters.command("profile") & filters.private)
@require_membership(callback_data="verify_profile")
async def profile_cmd(client, message):
    user = message.from_user
    if not user:
        return

    # ✅ Wajib punya username
    if not user.username:
        teks = (
            "┏━━━━━━━━━━━━━━━━━━━━━━\n"
            "┃ ⚠️ <b>TIDAK ADA USERNAME</b>\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "⚠️ Kamu belum memiliki <b>username Telegram</b>.\n\n"
            "👉 Silakan buat username terlebih dahulu di menu <b>Pengaturan Telegram</b>.\n"
            "Username ini berguna untuk menampilkan profil kamu dan menggunakan fitur bot dengan maksimal."
        )
        await message.reply(teks, parse_mode=ParseMode.HTML)
        return

    user_id = user.id
    username = user.username

    # Tambah XP (maks 1x per hari per command)
    await grant_xp_for_command(client, message, "profile")
    await update_supporter_badge(client, user_id)

    # Ambil data user
    data = load_user_data()
    info = data.get(str(user_id), {
        "username": username,
        "xp": 0,
        "badge": "Stranger 🔰",
        "last_xp_dates": {},
        "vip_unlocked": [],
        "supporter": False,
        "supporter_since": 0
    })
    
    key_balance = get_user_key(user_id)

    # XP & Badge
    xp = int(info.get("xp", 0))
    badge = _badge_for_xp(xp)
    supporter = info.get("supporter", False)
    supporter_since = int(info.get("supporter_since", 0))
    elapsed = int(time.time()) - supporter_since if supporter_since else 0

    # 🔹 Status supporter
    MIN_HOURS = 3
    if supporter:
        if supporter_since > 0:
            durasi_jam = elapsed // 3600
            durasi_menit = (elapsed % 3600) // 60
            if elapsed < MIN_HOURS * 3600:
                tunggu_menit = (MIN_HOURS * 3600 - elapsed) // 60
                supporter_status = (
                    f"✅ Aktif (baru {durasi_jam}j {durasi_menit}m, "
                    f"tunggu {tunggu_menit}m lagi untuk /claimbio)"
                )
            else:
                supporter_status = f"✅ Aktif (sejak {durasi_jam}j {durasi_menit}m lalu)"
        else:
            supporter_status = "✅ Aktif"
    else:
        supporter_status = "❌ Tidak"

    today = _now_jkt().strftime("%Y-%m-%d")
    next_name, remain = _next_tier_info(xp)
    progress = _progress_bar(xp)

    # Riwayat XP harian
    last_xp_dates = info.get("last_xp_dates", {})
    claimed_today = [cmd for cmd, d in last_xp_dates.items() if d == today]
    claimed_today.sort()
    claimed_count = len(claimed_today)
    xp_commands = [
        "profile", "ping", "random", "free", "lapor", "about", "listvip", "myvip", "start",
        "bot", "joinvip", "panduan", "search", "request", "freekey", "claim", "qris", "claimbio"
    ]
    max_daily = len(xp_commands)

    # Koleksi VIP
    unlocked = info.get("vip_unlocked", [])
    total_vip = len(unlocked)

    # 🔹 Bangun teks profil
    teks = (
        "👤 <b>PROFIL PENGGUNA</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<pre>"
        f"User       : @{username}\n"
        f"ID         : {user_id}\n"
        f"Badge      : {badge}\n"
        f"Saldo Key  : {key_balance} 🔑\n"
        f"Total XP   : {xp}\n"
        f"Progress   : {progress}\n"
    )
    if next_name:
        teks += f"Menuju     : ⬆️ {next_name} ({remain} XP lagi)\n"
    else:
        teks += "🚀 Kamu sudah di tier tertinggi!\n"
    teks += "</pre>"

    teks += (
        "🌻 <b>STATUS SUPORTER</b>\n"
        f"<pre>{supporter_status}</pre>\n"
    )

    # 🔹 Koleksi VIP
    teks += (
        "📂 <b>KOLEKSI VIP</b>\n"
        f"<pre>Total Koleksi : {total_vip}\n"
    )
    if unlocked:
        preview = ", ".join(unlocked[:5])  # batasi preview max 5
        if len(unlocked) > 5:
            preview += f", +{len(unlocked) - 5} lainnya..."
        teks += f"{preview}</pre>"

    # 🔹 Reward Badge
    teks += (
        "\n🏆 <b>REWARD NAIK BADGE</b>\n"
        "<pre>"
        "🔰 Stranger           : -\n"
        "🥉 Shimmer            : +2 🔑 Key\n"
        "🥈 Stellar            : +4 🔑 Key\n"
        "🥇 Starlord           : MEMBER VIP\n"
        "</pre>"
    )

    # 🔹 Klaim Key Mingguan
    teks += "🎁 <b>KLAIM KEY MINGGUAN</b>\n<pre>"
    can_claim, remaining = can_claim_weekly(user_id)
    keyboard = None
    if can_claim:
        teks += "✅ Kamu bisa klaim +2 Key gratis minggu ini!\n</pre>"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎁 Klaim Key Mingguan", callback_data="claim_weekly")],
            [InlineKeyboardButton("👑 Lihat Koleksi VIP", callback_data="verify_listvip")]
        ])
    else:
        days = remaining // 86400
        hours = (remaining % 86400) // 3600
        minutes = (remaining % 3600) // 60
        teks += f"⏳ Sudah klaim minggu ini.\nCoba lagi dalam {days}h {hours}j {minutes}m.\n</pre>"
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("👑 Lihat Koleksi VIP", callback_data="myvip_command")]
        ])

    # 🔹 Random Quota (blok)
    try:
        used, remaining_quota, limit, seconds = await get_random_quota_status(user_id)
        teks += f"🎲 <b>RANDOM</b>\n<pre>Sisa percobaan : {remaining_quota} (reset: {_format_eta(seconds)})\n</pre>"
    except Exception:
        pass

    # 🔹 Statistik Harian (blok)
    teks += (
        "📊 <b>XP HARIAN</b>\n"
        "<pre>"
        f"Daily XP  : {claimed_count} / {max_daily}\n"
    )
    if claimed_today:
        teks += "Sumber XP :\n" + ", ".join(f"/{c}" for c in claimed_today) + "\n"
    else:
        teks += "Sumber XP :\nbelum ada\n"
    teks += "</pre>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    await message.reply_text(teks, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    # Log ke admin
    await send_vip_log(
        client,
        (
            "👤 <b>PROFILE OPENED</b>\n"
            f"👤 User   : @{username} (ID: <code>{user_id}</code>)\n"
            f"🎖 Badge  : {badge}\n"
            f"✨ XP     : {xp}\n"
            f"🔑 Key    : {key_balance}\n"
            f"📦 Koleksi: {total_vip}\n"
        )
    )

# --- CLAIM VIA BIO (Daily referral claim) ---
# Paste ini di main.py (mis. dekat handler /claim)

def can_claim_daily_bio(user_id: int):
    """Return (can_claim: bool, remaining_seconds: int)."""
    data = load_user_data()
    user = data.get(str(user_id), {})
    last = int(user.get("last_daily_bio_claim", 0))
    now_ts = int(time.time())
    if now_ts - last >= 86400:
        return True, 0
    return False, 86400 - (now_ts - last)

def set_daily_bio_claim(user_id: int):
    data = load_user_data()
    user = data.get(str(user_id), {})
    user["last_daily_bio_claim"] = int(time.time())
    data[str(user_id)] = user
    save_user_data(data)

def normalize_text(txt: str) -> str:
    # lower + hilangkan simbol/emoji sederhana
    return re.sub(r"[^a-z0-9]+", " ", txt.lower())

def bio_matches(bio: str, tokens: list) -> bool:
    norm = normalize_text(bio)

    # cek exact tokens
    for t in tokens:
        if t in norm:
            return True

    # regex fuzzy: cari kata bangsa & bacol berdekatan
    if re.search(r"bangsa\s*bacol", norm):
        return True
    if re.search(r"bacol", norm):
        return True

    return False

def has_supporter_badge(user_id: int) -> bool:
    data = load_user_data()
    return bool(data.get(str(user_id), {}).get("supporter", False))

def set_supporter_badge(user_id: int, value: bool):
    data = load_user_data()
    user = data.get(str(user_id), {})
    user["supporter"] = value
    data[str(user_id)] = user
    save_user_data(data)

@app.on_message(filters.command("claimbio") & filters.private)
async def cmd_claim_bio(client, message: Message):
    await grant_xp_for_command(client, message, "claimbio")

    user = message.from_user
    if not user:
        return

    user_id = user.id
    username = user.username or "NoUsername"

    # 🔹 Update supporter badge dulu (cek bio + set supporter True/False)
    await update_supporter_badge(client, user_id)

    # 🚩 Cek supporter badge
    if not has_supporter_badge(user_id):
        return await message.reply(
            (
                "┏━━━━━━━━━━━━━━━\n"
                "┃ ⚠️ <b>Belum Supporter</b>\n"
                "┗━━━━━━━━━━━━━━━\n\n"
                "Kamu belum punya badge <b>Supporter ✅</b>.\n"
                "Tulis <code>@BangsaBacol</code> di bio Telegram kamu untuk mendapatkan badge <b>Supporter!</b>"
            ),
            parse_mode=ParseMode.HTML
        )
    
    # 🚩 Cek minimal durasi pasang bio
    MIN_HOURS = 12
    since = get_supporter_since(user_id)
    elapsed = int(time.time()) - since
    if elapsed < MIN_HOURS * 3600:
        remain = (MIN_HOURS * 3600 - elapsed) // 60
        return await message.reply(
            (
                "┏━━━━━━━━━━━━━━━\n"
                "┃ ⏳ <b>Belum Bisa Klaim</b>\n"
                "┗━━━━━━━━━━━━━━━\n\n"
                f"Badge Supporter baru aktif {elapsed//60} menit lalu.\n"
                f"Tunggu {remain} menit lagi sebelum bisa klaim."
            ),
            parse_mode=ParseMode.HTML
        )

    # cek cooldown harian
    can_claim, remaining = can_claim_daily_bio(user_id)
    if not can_claim:
        hours = remaining // 3600
        minutes = (remaining % 3600) // 60
        return await message.reply(
            (
                "┏━━━━━━━━━━━━━━━\n"
                "┃ ⏳ <b>Sudah Klaim Hari Ini</b>\n"
                "┗━━━━━━━━━━━━━━━\n\n"
                f"Kamu sudah klaim hari ini.\n"
                f"Coba lagi dalam {hours} jam {minutes} menit."
            ),
            parse_mode=ParseMode.HTML
        )

    # kasih reward
    add_user_key(user_id, 1)
    set_daily_bio_claim(user_id)
    saldo = get_user_key(user_id)

    teks = (
        "┏━━━━━━━━━━━━━━━\n"
        "┃ 🎉 <b>CLAIM BIO BERHASIL!</b>\n"
        "┗━━━━━━━━━━━━━━━\n\n"
        "🏅 Badge: <b>Supporter ✅</b>\n"
        "✅ +1 Key karena bio valid!\n"
        f"💰 Saldo sekarang: <b>{saldo} Key</b>"
    )
    await message.reply_text(teks, parse_mode=ParseMode.HTML)

    try:
        await send_vip_log(
            client,
            f"▶️ DAILY BIO CLAIM\nUser: @{username} (ID: <code>{user_id}</code>)\nReward: +1 Key\nSaldo: {saldo} Key"
        )
        await send_public_log(
            client,
            "claimbio",
            badge=load_user_data().get(str(user_id), {}).get("badge", "Stranger 🔰"),
            extra="+1 Key"
        )
    except Exception:
        pass

@app.on_message(filters.command("whois") & filters.private)
async def whois_cmd(client, message: Message):
    user = message.from_user
    if not user:
        return

    # ✅ hanya owner/admin yang bisa akses
    if not (is_owner(message) or is_admin(message)):
        await message.reply("❌ Hanya Owner/Admin yang bisa menggunakan command ini.")
        return

    args = message.text.split(maxsplit=1)
    if len(args) < 2:
        await message.reply(
            "⚠️ Format salah.\n\n"
            "Gunakan:\n"
            "<code>/whois @username</code> atau <code>/whois 123456789</code>",
            parse_mode=ParseMode.HTML
        )
        return

    target_arg = args[1].lstrip("@")

    # 🚩 Cari data user berdasarkan ID atau username
    data = load_user_data()
    target_info = None
    target_id = None

    if target_arg.isdigit():
        # input berupa ID
        if target_arg in data:
            target_info = data[target_arg]
            target_id = int(target_arg)
    else:
        # input berupa username
        for uid, info in data.items():
            username_db = info.get("username") or ""
            if username_db.lower() == target_arg.lower():
                target_info = info
                target_id = int(uid)
                break

    if not target_info:
        await message.reply(f"❌ User @{target_username} tidak ditemukan di database.")
        return

    # Ambil data
    xp = int(target_info.get("xp", 0))
    badge = _badge_for_xp(xp)
    key_balance = get_user_key(target_id)
    unlocked = target_info.get("vip_unlocked", [])
    total_vip = len(unlocked)

    today = _now_jkt().strftime("%Y-%m-%d")
    last_xp_dates = target_info.get("last_xp_dates", {})
    claimed_today = [cmd for cmd, d in last_xp_dates.items() if d == today]
    claimed_today.sort()
    claimed_count = len(claimed_today)

    next_name, remain = _next_tier_info(xp)
    progress = _progress_bar(xp)

    # 🔹 Teks profil target (versi detail kaya profile)
    teks = (
        "👤 <b>PROFIL MEMBER</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<pre>"
        f"User       : @{target_info.get('username') or 'NoUsername'}\n"
        f"ID         : {target_id}\n"
        f"Badge      : {badge}\n"
        f"Saldo Key  : {key_balance} 🔑\n"
        f"Total XP   : {xp}\n"
        f"Progress   : {progress}\n"
    )
    if next_name:
        teks += f"Menuju     : ⬆️ {next_name} ({remain} XP lagi)\n"
    else:
        teks += "🚀 Sudah di tier tertinggi!\n"
    teks += "</pre>"

    # 🔹 Status supporter
    supporter = target_info.get("supporter", False)
    supporter_since = int(target_info.get("supporter_since", 0))
    elapsed = int(time.time()) - supporter_since if supporter_since else 0
    if supporter:
        if supporter_since > 0:
            durasi_jam = elapsed // 3600
            durasi_menit = (elapsed % 3600) // 60
            supporter_status = f"✅ Aktif (sejak {durasi_jam}j {durasi_menit}m lalu)"
        else:
            supporter_status = "✅ Aktif"
    else:
        supporter_status = "❌ Tidak"

    teks += (
        "🌻 <b>STATUS SUPORTER</b>\n"
        f"<pre>{supporter_status}</pre>\n"
    )

    # 🔹 Koleksi VIP
    teks += (
        "📂 <b>KOLEKSI VIP</b>\n"
        f"<pre>Total Koleksi : {total_vip}\n"
    )
    if unlocked:
        preview = ", ".join(unlocked[:5])
        if len(unlocked) > 5:
            preview += f", +{len(unlocked) - 5} lainnya..."
        teks += f"{preview}</pre>"

    # 🔹 Reward Badge
    teks += (
        "\n🏆 <b>REWARD NAIK BADGE</b>\n"
        "<pre>"
        "🔰 Stranger           : -\n"
        "🥉 Shimmer            : +2 🔑 Key\n"
        "🥈 Stellar            : +4 🔑 Key\n"
        "🥇 Starlord           : MEMBER VIP\n"
        "</pre>"
    )

    # 🔹 Statistik XP Harian
    teks += (
        "📊 <b>XP HARIAN</b>\n"
        "<pre>"
        f"Daily XP  : {claimed_count}\n"
    )
    if claimed_today:
        teks += "Sumber XP :\n" + ", ".join(f"/{c}" for c in claimed_today) + "\n"
    else:
        teks += "Sumber XP :\nbelum ada\n"
    teks += "</pre>━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    await message.reply(teks, parse_mode=ParseMode.HTML)

    # 🚩 Log admin
    await send_vip_log(
        client,
        (
            "👤 <b>WHOIS CHECKED</b>\n"
            f"👤 Requestor : @{user.username or 'NoUsername'} (ID: <code>{user.id}</code>)\n"
            f"🔍 Target    : @{target_info.get('username') or 'NoUsername'} (ID: <code>{target_id}</code>)\n"
            f"🎖 Badge     : {badge}\n"
            f"✨ XP        : {xp}\n"
            f"🔑 Key       : {key_balance}\n"
            f"📦 Koleksi   : {total_vip}\n"
        )
    )

# =====================================================
# TOPUP WIZARD
# =====================================================
TOPUP_SESSIONS: Dict[int, Dict[str, any]] = {}
TOPUP_SESSION_TIMEOUT = 300  # detik

def _clear_topup_session(admin_id: int):
    if admin_id in TOPUP_SESSIONS:
        task = TOPUP_SESSIONS[admin_id].get("task")
        if isinstance(task, asyncio.Task) and not task.done():
            task.cancel()
        TOPUP_SESSIONS.pop(admin_id, None)

async def _topup_session_timeout(admin_id: int, timeout: int = TOPUP_SESSION_TIMEOUT):
    try:
        await asyncio.sleep(timeout)
        if admin_id in TOPUP_SESSIONS:
            TOPUP_SESSIONS.pop(admin_id, None)
    except asyncio.CancelledError:
        return

@app.on_message(filters.command("topup") & filters.private)
async def cmd_topup_start(client, message):
    admin_id = message.from_user.id

    if not (is_owner(message) or is_admin(message)):
        await message.reply("❌ Hanya Owner/Admin yang boleh melakukan topup.")
        return

    if admin_id in TOPUP_SESSIONS:
        await message.reply("⚠️ Kamu sedang memiliki session topup aktif. Ketik /canceltopup untuk batalkan.")
        return

    TOPUP_SESSIONS[admin_id] = {"step": "await_target", "target_uid": None, "amount": None}
    TOPUP_SESSIONS[admin_id]["task"] = asyncio.create_task(_topup_session_timeout(admin_id))

    await message.reply(
        "🔰 *Topup Key — Step 1/3*\n\n"
        "Silakan kirim <b>User ID</b> target atau <b>@username</b>.\n\n"
        "Ketik /canceltopup untuk membatalkan.",
        parse_mode=ParseMode.HTML
    )

async def topup_session_handler(client, message):
    admin_id = message.from_user.id
    if admin_id not in TOPUP_SESSIONS:
        return

    session = TOPUP_SESSIONS[admin_id]
    step = session.get("step")

    if message.text and message.text.strip().lower() in ("/canceltopup", "/cancel_topup"):
        _clear_topup_session(admin_id)
        await message.reply("✅ Session topup dibatalkan.")
        return

    if step == "await_target":
        text = (message.text or "").strip()
        if not text:
            await message.reply("❌ Input kosong. Kirim User ID atau @username target.")
            return

        target_uid = None
        if text.startswith("@"):
            try:
                user_obj = await client.get_users(text)
                target_uid = int(user_obj.id)
            except Exception as e:
                await message.reply(f"❌ Gagal resolve username: {e}")
                return
        else:
            try:
                target_uid = int(text)
            except ValueError:
                await message.reply("❌ Format salah. Kirim angka User ID atau @username.")
                return

        session["target_uid"] = target_uid
        session["step"] = "await_amount"

        await message.reply(
            f"🔰 *Topup Key — Step 2/3*\n\nTarget: <code>{target_uid}</code>\n\n"
            "Sekarang kirim jumlah Key (angka bulat positif), mis: `10`",
            parse_mode=ParseMode.HTML
        )
        return

    if step == "await_amount":
        text = (message.text or "").strip()
        try:
            amount = int(text)
            if amount <= 0:
                raise ValueError()
        except Exception:
            await message.reply("❌ Jumlah tidak valid. Kirim angka bulat > 0.")
            return

        session["amount"] = amount
        session["step"] = "await_confirm"

        task = session.get("task")
        if isinstance(task, asyncio.Task) and not task.done():
            task.cancel()

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Konfirmasi Topup", callback_data=f"topup_confirm|{admin_id}|{session['target_uid']}|{amount}")],
            [InlineKeyboardButton("❌ Batal", callback_data=f"topup_cancel|{admin_id}")]
        ])
        await message.reply(
            f"🔰 *Topup Key — Step 3/3*\n\n"
            f"Target : <code>{session['target_uid']}</code>\n"
            f"Jumlah : <b>{amount}</b> Key\n\n"
            "Tekan *Konfirmasi* untuk eksekusi atau *Batal*.",
            parse_mode=ParseMode.HTML,
            reply_markup=kb
        )

# -----------------------
# 3) Callback handler: konfirmasi / batal
# -----------------------
@app.on_callback_query(filters.regex(r"^topup_confirm\|") | filters.regex(r"^topup_cancel\|"))
async def handle_topup_confirm_cancel(client: Client, cq: CallbackQuery):
    data = cq.data or ""
    parts = data.split("|")
    action = parts[0]  # topup_confirm or topup_cancel

    # parsing safe
    if action == "topup_confirm" and len(parts) == 4:
        _, admin_id_s, target_uid_s, amount_s = parts
        try:
            admin_id = int(admin_id_s)
            target_uid = int(target_uid_s)
            amount = int(amount_s)
        except Exception:
            await cq.answer("Data konfirmasi tidak valid.", show_alert=True)
            return

        # hanya admin yg memulai session atau owner/admin boleh mengeksekusi
        caller = cq.from_user.id
        if caller != admin_id and not (is_owner(cq) or is_admin(cq)):
            await cq.answer("❌ Kamu tidak berwenang untuk konfirmasi topup ini.", show_alert=True)
            return

        # pastikan session masih ada dan cocok
        session = TOPUP_SESSIONS.get(admin_id)
        if not session or session.get("target_uid") != target_uid or session.get("amount") != amount:
            _clear_topup_session(admin_id)
            await cq.answer("❌ Session sudah kadaluarsa atau tidak ditemukan.", show_alert=True)
            return

        # lakukan topup: update file user_data
        try:
            data = load_user_data()
            user = data.get(str(target_uid), {
                "username": None,
                "xp": 0,
                "badge": "Stranger 🔰",
                "last_xp_dates": {},
                "key": 0
            })
            user["key"] = user.get("key", 0) + amount
            data[str(target_uid)] = user
            save_user_data(data)
        except Exception as e:
            logger.error(f"Topup gagal saat menyimpan data: {e}")
            await cq.answer(f"❌ Gagal menyimpan data: {e}", show_alert=True)
            return

        # sukses
        _clear_topup_session(admin_id)
        await cq.message.edit_text(
            f"✅ Topup berhasil: <code>{amount}</code> Key ditambahkan ke <code>{target_uid}</code>.",
            parse_mode=ParseMode.HTML
        )

        # kirim log publik anonim
        await send_public_log(client, "topup", badge=user["badge"], extra=f"{amount} 🔑")

        try:
            # opsi: kirimkan pemberitahuan ke user target (jika bot dapat mengirimkan PM)
            await client.send_message(
                target_uid,
                (
                    "🎉✨ <b>TOP UP KEY BERHASIL!</b> ✨🎉\n"
                    "<pre>"
                    f"🔑 Jumlah Key : {amount} Key\n"
                    "👑 Dari       : BangsaBacol\n"
                    "🔐 Fungsi    : Unlock koleksi premium\n"
                    "</pre>"
                    "🚀 <b>Langsung Gunakan:</b>\n"
                    "👤 Cek /profile untuk informasi lebih detail.\n"
                    "📜 Masuk /listvip dan buka koleksi favoritmu.\n\n"
                    "💡 <b>Tips & Info Penting:</b>\n"
                    "<pre>"
                    "• Key bersifat personal khusus buat kamu.\n"
                    "• Cek jumlah Key sebelum unlock koleksi.\n"
                    "• Koleksi tiap hari terus bertambah.\n"
                    "• Jika ada kendala, hubungi Admin-Pusat.\n"
                    "</pre>\n"
                    "<b>Bantuan dan Dukungan:</b>\n"
                    "💌 <a href='https://t.me/BangsaBacol_Bot?start=lapor'>Lapor ke Admin</a> | "
                    "📜 <a href='https://t.me/BangsaBacol/8'>Daftar Bantuan</a>\n"
                    "💦 Terima kasih sudah menggunakan layanan kami!"
                ),
                parse_mode=ParseMode.HTML, disable_web_page_preview=True
            )
        except Exception:
            # abaikan jika gagal kirim ke user (mis user belum pernah chat dengan bot)
            pass

        await cq.answer("Topup selesai.", show_alert=False)
        return

# =====================================================
# GIFT KEY WIZARD (FINAL, 3 TEMPLATE)
# =====================================================
GIFT_SESSIONS: Dict[int, Dict[str, any]] = {}
GIFT_SESSION_TIMEOUT = 300  # detik


def _clear_gift_session(admin_id: int):
    if admin_id in GIFT_SESSIONS:
        task = GIFT_SESSIONS[admin_id].get("task")
        if isinstance(task, asyncio.Task) and not task.done():
            task.cancel()
        GIFT_SESSIONS.pop(admin_id, None)


async def _gift_session_timeout(admin_id: int, timeout: int = GIFT_SESSION_TIMEOUT):
    try:
        await asyncio.sleep(timeout)
        if admin_id in GIFT_SESSIONS:
            GIFT_SESSIONS.pop(admin_id, None)
    except asyncio.CancelledError:
        return


# -----------------------
# COMMAND START
# -----------------------
@app.on_message(filters.command("giftkey") & filters.private)
async def cmd_giftkey_start(client, message):
    admin_id = message.from_user.id

    if not (is_owner(message) or is_admin(message)):
        await message.reply("❌ Hanya Owner/Admin yang boleh memberikan Gift.")
        return

    if admin_id in GIFT_SESSIONS:
        await message.reply("⚠️ Kamu sedang memiliki session Gift aktif. Ketik /cancelgift untuk batalkan.")
        return

    GIFT_SESSIONS[admin_id] = {
        "step": "await_target",
        "target_uid": None,
        "amount": None,
        "template": None,
        "custom_text": None,
        "task": asyncio.create_task(_gift_session_timeout(admin_id)),
    }

    await message.reply(
        "🎁 *Gift Key — Step 1/3*\n\n"
        "Silakan kirim <b>User ID</b> target atau <b>@username</b>.\n\n"
        "Ketik /cancel_gift untuk membatalkan.",
        parse_mode=ParseMode.HTML
    )


# -----------------------
# SESSION HANDLER
# -----------------------
async def gift_session_handler(client, message):
    admin_id = message.from_user.id
    print(f"[GIFT DEBUG] masuk handler dari {admin_id}, text={message.text}")

    if admin_id not in GIFT_SESSIONS:
        print("[GIFT DEBUG] session tidak ditemukan")
        return

    session = GIFT_SESSIONS[admin_id]
    step = session.get("step")

    # batal manual
    if message.text and message.text.strip().lower() in ("/cancelgift", "/cancel_gift"):
        _clear_gift_session(admin_id)
        await message.reply("✅ Session Gift dibatalkan.")
        return

    # step target
    if step == "await_target":
        text = (message.text or "").strip()
        if not text:
            await message.reply("❌ Input kosong. Kirim User ID atau @username target.")
            return

        target_uid = None
        if text.startswith("@"):
            try:
                user_obj = await client.get_users(text)
                target_uid = int(user_obj.id)
            except Exception as e:
                await message.reply(f"❌ Gagal resolve username: {e}")
                return
        else:
            try:
                target_uid = int(text)
            except ValueError:
                await message.reply("❌ Format salah. Kirim angka User ID atau @username.")
                return

        session["target_uid"] = target_uid
        session["step"] = "await_amount"

        await message.reply(
            f"🎁 *Gift Key — Step 2/3*\n\nTarget: <code>{target_uid}</code>\n\n"
            "Sekarang kirim jumlah Key (boleh 0), mis: `10`",
            parse_mode=ParseMode.HTML
        )
        return

    # step amount
    if step == "await_amount":
        text = (message.text or "").strip()
        try:
            amount = int(text)
            if amount < 0:
                raise ValueError()
        except Exception:
            await message.reply("❌ Jumlah tidak valid. Kirim angka bulat ≥ 0.")
            return

        session["amount"] = amount
        session["step"] = "await_template"

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎁 Gift Umum", callback_data=f"gift_template1|{admin_id}")],
            [InlineKeyboardButton("✅ Approved", callback_data=f"gift_template2|{admin_id}")],
            [InlineKeyboardButton("❌ Rejected", callback_data=f"gift_template3|{admin_id}")]
        ])
        await message.reply(
            f"🎁 Pilih template Gift untuk target <code>{session['target_uid']}</code>.\n"
            f"Jumlah Key: <b>{amount}</b>\n\n"
            "Pilih salah satu template di bawah:",
            parse_mode=ParseMode.HTML,
            reply_markup=kb
        )
        return

    # step confirm: admin kirim custom text opsional (Template 1, 2, atau 3)
    if step == "await_confirm" and session.get("template") in (1, 2, 3):
        text = (message.text or "").strip()
        if text:
            session["custom_text"] = text
            await message.reply(
                f"✍️ Pesan custom untuk Template {session['template']} diset ke:\n\n"
                f"<i>{text}</i>\n\n"
                "👉 Sekarang tekan *Konfirmasi* untuk kirim Gift.",
                parse_mode=ParseMode.HTML
            )
        else:
            await message.reply("❌ Pesan kosong tidak disimpan. Klik *Konfirmasi* atau kirim ulang pesan custom.")
        return
    
# -----------------------
# CALLBACK: pilih template
# -----------------------
@app.on_callback_query(filters.regex(r"^gift_template"))
async def handle_gift_template(client: Client, cq: CallbackQuery):
    data = cq.data or ""
    parts = data.split("|")
    template = parts[0]
    admin_id = int(parts[1])
    session = GIFT_SESSIONS.get(admin_id)

    if not session:
        await cq.answer("❌ Session sudah tidak aktif.", show_alert=True)
        return

    if template == "gift_template1":
        session["template"] = 1
        session["step"] = "await_confirm"
        session["custom_text"] = None
        msg = "🎁 *Template 1 (Gift Umum)*\n\n👉 Bisa tambah pesan custom opsional. Kalau tidak, dipakai default."
    elif template == "gift_template2":
        session["template"] = 2
        session["step"] = "await_confirm"
        session["custom_text"] = None
        msg = "✅ *Template 2 (Approved)*\n\n👉 Bisa tambah pesan custom opsional. Kalau tidak, dipakai default."
    elif template == "gift_template3":
        session["template"] = 3
        session["step"] = "await_confirm"
        session["custom_text"] = None
        msg = "❌ *Template 3 (Rejected)*\n\n👉 Bisa tambah alasan custom opsional. Kalau tidak, dipakai default."
    else:
        return

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Konfirmasi Gift", callback_data=f"gift_confirm|{admin_id}|{session['target_uid']}|{session['amount']}")],
        [InlineKeyboardButton("❌ Batal", callback_data=f"gift_cancel|{admin_id}")]
    ])
    await cq.message.edit_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

# -----------------------
# CALLBACK: konfirmasi / batal
# -----------------------
@app.on_callback_query(filters.regex(r"^gift_confirm\|") | filters.regex(r"^gift_cancel\|"))
async def handle_gift_confirm_cancel(client: Client, cq: CallbackQuery):
    data = cq.data or ""
    parts = data.split("|")
    action = parts[0]

    if action == "gift_confirm" and len(parts) == 4:
        _, admin_id_s, target_uid_s, amount_s = parts
        try:
            admin_id = int(admin_id_s)
            target_uid = int(target_uid_s)
            amount = int(amount_s)
        except Exception:
            await cq.answer("Data konfirmasi tidak valid.", show_alert=True)
            return

        caller = cq.from_user.id
        if caller != admin_id and not (is_owner(cq) or is_admin(cq)):
            await cq.answer("❌ Kamu tidak berwenang untuk konfirmasi Gift ini.", show_alert=True)
            return

        session = GIFT_SESSIONS.get(admin_id)
        if not session or session.get("target_uid") != target_uid or session.get("amount") != amount:
            _clear_gift_session(admin_id)
            await cq.answer("❌ Session sudah kadaluarsa atau tidak ditemukan.", show_alert=True)
            return

        # siapkan caption sesuai template
        tpl = session.get("template", 1)
        custom_text = session.get("custom_text", "")

        if tpl == 1:
            caption = (
                "🎁✨ <b>SELAMAT KAMU DAPAT KEY!</b> ✨🎁\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🔑 Jumlah Key: <b>{amount} Key</b>\n"
                "💌 Dari: <b>Admin-Pusat</b>\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "💭 <b>Pesan untukmu:</b>\n"
                f"<i>{custom_text or 'Terimakasih sudah menjadi bagian dari Bangsa Bacol. Jika kamu aktif dan sering berinteraksi disini, Admin-Pusat akan memberikan hadiah <b>Key</b> untukmu!🥰'}</i>\n\n"
                "🚀 <b>Langsung gunakan:</b>\n"
                "👤 Cek /profile untuk informasi lebih detail.\n"
                "📜 Masuk /listvip untuk buka koleksi favoritmu.\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "💦 Selamat menikmati hadiahmu!"
            )
        elif tpl == 2:
            caption = (
                "✅✨ <b>KABAR GEMBIRA!</b> ✨✅\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🔑 Reward: {amount} Key\n"
                "💌 Dari: Admin-Pusat\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "💭 <b>Pesan untukmu:</b>\n"
                f"<i>{custom_text or 'Terimakasih telah ikut berkontribusi di Bangsa Bacol. Mantab! kamu paham dengan sistem /freekey, Koleksi kamu lolos dan disetujui, hadiah ini spesial buat kamu! Siapkan koleksi selanjutnya ya!🥰'}</i>\n\n"
                "🚀 <b>Langsung gunakan:</b>\n"
                "👤 Cek /profile untuk informasi lebih detail.\n"
                "📜 Masuk /listvip untuk buka koleksi favoritmu.\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "💦 Selamat menikmati hadiahmu!"
            )
        elif tpl == 3:
            caption = (
                "❌💥 <b>KABAR BURUK!</b> 💥❌\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🔑 Reward: {amount} Key\n"
                "💌 Dari: Admin-Pusat\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "💭 <b>Alasan:</b>\n"
                f"<i>{custom_text or 'Koleksi yang kamu kirim belum memenuhi standar Bangsa Bacol (kamu bisa baca ulang syarat dan ketentuannya di /freekey ya). Siapkan koleksi terbaikmu selanjutnya dan silahkan coba lagi!🥰'}</i>\n\n"
                "🚀 <b>Langsung gunakan:</b>\n"
                "👤 Cek /profile untuk informasi lebih detail.\n"
                "📜 Masuk /listvip untuk buka koleksi favoritmu.\n"
                "━━━━━━━━━━━━━━━━━━━━━━━━\n"
                "🥰 Tetap semangat kirim koleksi terbaikmu!"
            )
        else:
            caption = "🎁 Gift Key"

        # update data user (meskipun amount=0 tetap simpan)
        try:
            data = load_user_data()
            user = data.get(str(target_uid), {
                "username": None,
                "xp": 0,
                "badge": "Stranger 🔰",
                "last_xp_dates": {},
                "key": 0
            })
            user["key"] = user.get("key", 0) + amount
            data[str(target_uid)] = user
            save_user_data(data)
        except Exception as e:
            logger.error(f"Gift gagal saat menyimpan data: {e}")
            await cq.answer(f"❌ Gagal menyimpan data: {e}", show_alert=True)
            return

        _clear_gift_session(admin_id)
        await cq.message.edit_text(
            f"✅ Gift berhasil: <code>{amount}</code> Key dikirim ke <code>{target_uid}</code>.",
            parse_mode=ParseMode.HTML
        )

        try:
            await client.send_message(target_uid, caption, parse_mode=ParseMode.HTML)
        except Exception:
            pass
        
        # 🚩 Log publik anonim (biar anggota lain ketrigger)
        try:
            await send_public_log(
                client,
                "gift",
                badge=user.get("badge", "Stranger 🔰"),
                extra=f"{amount} 🔑"
            )
        except Exception as e:
            logger.error(f"Public log gift gagal: {e}")

        await cq.answer("Gift selesai.", show_alert=False)
        return

    if action == "gift_cancel":
        try:
            admin_id = int(parts[1])
        except:
            return
        _clear_gift_session(admin_id)
        await cq.message.edit_text("❌ Gift dibatalkan oleh Admin.")
        await cq.answer("Gift dibatalkan.", show_alert=False)

# =====================================================
# RESET KEY WIZARD
# =====================================================
RESET_SESSIONS = {}

async def _reset_session_timeout(admin_id: int, seconds: int = 120):
    await asyncio.sleep(seconds)
    if admin_id in RESET_SESSIONS:
        RESET_SESSIONS.pop(admin_id, None)

def _clear_reset_session(admin_id: int):
    sess = RESET_SESSIONS.pop(admin_id, None)
    if sess and isinstance(sess.get("task"), asyncio.Task):
        sess["task"].cancel()

@app.on_message(filters.command("resetkey") & filters.private)
async def cmd_resetkey_start(client, message):
    admin_id = message.from_user.id
    if not (is_owner(message) or is_admin(message)):
        await message.reply("❌ Hanya Owner/Admin yang boleh reset key.")
        return

    if admin_id in RESET_SESSIONS:
        await message.reply("⚠️ Kamu masih punya session resetkey aktif.")
        return

    RESET_SESSIONS[admin_id] = {"step": "await_target"}
    RESET_SESSIONS[admin_id]["task"] = asyncio.create_task(_reset_session_timeout(admin_id))

    await message.reply(
        "🔰 <b>Reset Key — Step 1/3</b>\n\n"
        "Kirim User ID atau @username target.",
        parse_mode=ParseMode.HTML
    )

async def resetkey_session_handler(client, message):
    admin_id = message.from_user.id
    if admin_id not in RESET_SESSIONS:
        return

    sess = RESET_SESSIONS[admin_id]
    step = sess.get("step")

    if message.text and message.text.strip().lower() == "/cancelreset":
        _clear_reset_session(admin_id)
        await message.reply("✅ Session resetkey dibatalkan.")
        return

    # === STEP 1: Dapatkan target ===
    if step == "await_target":
        text = (message.text or "").strip()
        target_uid = None
        if text.startswith("@"):
            try:
                user_obj = await client.get_users(text)
                target_uid = int(user_obj.id)
            except:
                await message.reply("❌ Username tidak valid.")
                return
        else:
            try:
                target_uid = int(text)
            except:
                await message.reply("❌ ID tidak valid.")
                return

        # Ambil saldo awal
        datau = load_user_data()
        user = datau.get(str(target_uid), {})
        current_key = user.get("key", 0)

        sess["target_uid"] = target_uid
        sess["current_key"] = current_key
        sess["step"] = "await_mode"

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🟢 Reset ke 0", callback_data=f"reset_mode|{admin_id}|{target_uid}|0")],
            [InlineKeyboardButton("✏️ Custom Reset", callback_data=f"reset_mode|{admin_id}|{target_uid}|custom")],
            [InlineKeyboardButton("❌ Batal", callback_data=f"reset_cancel|{admin_id}")]
        ])
        await message.reply(
            f"🔰 <b>Reset Key — Step 2/3</b>\n\n"
            f"👤 Target: <code>{target_uid}</code>\n"
            f"💰 Saldo saat ini: <b>{current_key}</b>\n\n"
            "Pilih mode reset:",
            parse_mode=ParseMode.HTML,
            reply_markup=kb
        )

    # === STEP 3: Input custom jumlah ===
    elif step == "await_custom":
        try:
            custom_value = int(message.text.strip())
        except ValueError:
            await message.reply("❌ Masukkan angka valid.")
            return

        sess["custom_value"] = custom_value
        sess["step"] = "await_confirm"

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Konfirmasi", callback_data=f"reset_confirm|{admin_id}|{sess['target_uid']}|{custom_value}")],
            [InlineKeyboardButton("❌ Batal", callback_data=f"reset_cancel|{admin_id}")]
        ])

        await message.reply(
            f"🔰 <b>Reset Key — Step 3/3</b>\n\n"
            f"👤 Target: <code>{sess['target_uid']}</code>\n"
            f"💰 Saldo saat ini: <b>{sess['current_key']}</b>\n"
            f"🎯 Saldo baru: <b>{custom_value}</b> (akan <b>{'diset langsung' if custom_value >= 0 else f'dikurangi {abs(custom_value)}'}</b>)",
            parse_mode=ParseMode.HTML,
            reply_markup=kb
        )

@app.on_callback_query(filters.regex(r"^reset_mode\\|") | filters.regex(r"^reset_confirm\\|") | filters.regex(r"^reset_cancel\\|"))
async def handle_reset_confirm_cancel(client, cq: CallbackQuery):
    data = cq.data.split("|")
    action = data[0]

    # === PILIH MODE ===
    if action == "reset_mode" and len(data) == 4:
        _, admin_id_s, target_uid_s, mode = data
        admin_id, target_uid = int(admin_id_s), int(target_uid_s)
        sess = RESET_SESSIONS.get(admin_id, {})
        sess["target_uid"] = target_uid

        if mode == "0":
            sess["custom_value"] = 0
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Konfirmasi", callback_data=f"reset_confirm|{admin_id}|{target_uid}|0")],
                [InlineKeyboardButton("❌ Batal", callback_data=f"reset_cancel|{admin_id}")]
            ])
            await cq.message.edit_text(
                f"🔰 <b>Reset Key — Step 3/3</b>\n\n"
                f"👤 Target: <code>{target_uid}</code>\n"
                f"💰 Saldo saat ini: <b>{sess.get('current_key', 0)}</b>\n"
                "🎯 Saldo baru: <b>0</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=kb
            )

        elif mode == "custom":
            sess["step"] = "await_custom"
            await cq.message.edit_text(
                f"✏️ Kirim jumlah key baru (contoh: <code>5</code> untuk set saldo ke 5, <code>-3</code> untuk kurangi 3).\n\n"
                f"Saldo saat ini: <b>{sess.get('current_key', 0)}</b>",
                parse_mode=ParseMode.HTML
            )
        return

    # === KONFIRMASI EKSEKUSI ===
    if action == "reset_confirm" and len(data) == 4:
        _, admin_id_s, target_uid_s, value_s = data
        admin_id, target_uid = int(admin_id_s), int(target_uid_s)
        try:
            custom_value = int(value_s)
        except ValueError:
            await cq.answer("❌ Nilai tidak valid.", show_alert=True)
            return

        if cq.from_user.id != admin_id and not (is_owner(cq) or is_admin(cq)):
            await cq.answer("❌ Tidak berwenang.", show_alert=True)
            return

        try:
            datau = load_user_data()
            user = datau.get(str(target_uid), {"key": 0})
            if custom_value >= 0:
                user["key"] = custom_value
            else:
                user["key"] = max(0, user.get("key", 0) + custom_value)
            datau[str(target_uid)] = user
            save_user_data(datau)
        except Exception as e:
            await cq.answer(f"❌ Error: {e}", show_alert=True)
            return

        _clear_reset_session(admin_id)
        await cq.message.edit_text(
            f"✅ Key user <code>{target_uid}</code> berhasil diset ke <b>{user['key']}</b>.",
            parse_mode=ParseMode.HTML
        )
        await cq.answer("Reset selesai.")
        return

    # === BATAL ===
    if action == "reset_cancel":
        _, admin_id_s = data
        admin_id = int(admin_id_s)
        _clear_reset_session(admin_id)
        await cq.message.edit_text("❌ Reset key dibatalkan.")
        await cq.answer("Session reset dibatalkan.")

# =====================================================
# KEY COMMAND (USER & ADMIN)
# =====================================================
KEY_SESSIONS = {}

@app.on_message(filters.command("key") & filters.private)
async def cmd_key_start(client, message):
    user_id = message.from_user.id

    if not (is_owner(message) or is_admin(message)) or len(message.command) == 1:
        data = load_user_data()
        saldo = data.get(str(user_id), {}).get("key", 0)
        await message.reply(f"🔑 Saldo Key kamu: <b>{saldo}</b>", parse_mode=ParseMode.HTML)
        return

    await message.reply("🔎 Kirim User ID atau @username target untuk cek saldo Key.")
    KEY_SESSIONS[user_id] = True

async def key_session_handler(client, message):
    admin_id = message.from_user.id
    if admin_id not in KEY_SESSIONS:
        return
    KEY_SESSIONS.pop(admin_id, None)

    try:
        target_arg = message.text.strip()
        if target_arg.startswith("@"):
            user_obj = await client.get_users(target_arg)
            target_uid = int(user_obj.id)
        else:
            target_uid = int(target_arg)

        data = load_user_data()
        info = data.get(str(target_uid), {})
        saldo = info.get("key", 0)
        uname = info.get("username") or "-"

        await message.reply(
            f"👤 User: <code>{target_uid}</code> (@{uname})\n🔑 Saldo Key: <b>{saldo}</b>",
            parse_mode=ParseMode.HTML
        )

    except Exception as e:
        await message.reply(f"⚠️ Gagal membaca input: <code>{e}</code>", parse_mode=ParseMode.HTML)

# ================================
# FREEKEY FLOW (session)
# ================================
FREEKEY_SESSIONS = {}  # FREEKEY_SESSIONS[user_id] = {"step": ..., "count": 0, "media_ids": [], "nama": None, "is_real_name": None, "publish_mode": None}

@app.on_message(filters.command("freekey") & filters.private)
@require_membership(callback_data="verify_freekey")
async def freekey_command(client, message: Message):
    user_id = message.from_user.id
    username = message.from_user.username or "NoUsername"
    FREEKEY_SESSIONS[user_id] = {
        "step": "STEP1_RULES",
        "count": 0,
        "media_ids": [],
        "user_info": {
            "username": message.from_user.username or None,
            "first_name": message.from_user.first_name or "No Name"
        }
    }

    # (opsional) XP tracking
    try:
        await grant_xp_for_command(client, message, "freekey")
    except Exception:
        pass

    # kirim log ke admin
    if LOG_CHANNEL_ID:
        try:
            await client.send_message(
                LOG_CHANNEL_ID,
                (
                    "📥 <b>FreeKey Started</b>\n"
                    f"├ 👤 User: @{username}\n"
                    f"├ 🆔 ID  : <code>{user_id}</code>\n"
                    "└ 💬 Command: /freekey"
                ),
                parse_mode=ParseMode.HTML
            )
        except Exception:
            pass

    text = (
        "┏━━━✨ <b>SISTEM FREE KEY</b> ✨━━━┓\n\n"

        "📌 Dengan <b>FreeKey</b> kamu bisa menambahkan koleksi pribadi ke <b>Bangsa Bacol</b>.\n"
        "Semakin lengkap & berkualitas koleksimu → semakin besar imbalannya!\n\n"

        "🔑 <b>CARA KERJA:</b>\n"
        "<pre>"
        "1. Kamu upload koleksi (foto/video/dokumen) ke Bangsa Bacol.\n"
        "2. Tentukan Mode koleksimu: <i>Private</i> atau <i>Publik</i>\n"
        "3. Admin akan meninjau kualitas koleksimu.\n"
        "4. Reward <b>KEY</b> berdasarkan jumlah & kualitas.\n"
        "</pre>"
        "📦 <b>REWARD DASAR:</b>\n"
        "<pre>"
        "•   1 – 10 media   :  1 –  3 Key 🔑\n"
        "•  11 – 20 media   :  3 –  5 Key 🔑\n"
        "•  21 – 50 media   :  5 – 10 Key 🔑\n"
        "•  51 – 100 media  : 10 – 20 Key 🔑\n"
        "•  100+ media      : 20 – 50 Key 🔑\n"
        "</pre>"
        "🎁 <b>BONUS KUALITAS:</b>\n"
        "<pre>"
        "+1 KEY → identitas / username jelas\n"
        "+1 KEY → koleksi rare / unik\n"
        "+1 KEY → kualitas HD / full album\n"
        "+1 KEY → koleksi pribadi\n"
        "</pre>"
        "📜 <b>KHUSUS KOLPRI:</b>\n"
        "<pre>"
        "Jika kamu submit koleksi pribadimu sendiri, "
        "kamu juga akan terus mendapat <b>Key</b> ketika koleksimu di unlock Member lain. Reward: 1 unlock = 1 Key 🔑✨\n"
        "</pre>"
        "💡 <b>CATATAN:</b>\n"
        "<pre>"
        "• Admin menilai <b>jumlah + kualitas</b>, bukan sekadar banyaknya.\n"
        "• Koleksi sedikit tapi rare/HD bisa tetap dapat reward besar.\n"
        "• Koleksi ditolak = user dapat notifikasi + alasan umum.\n"
        "• Semua koleksi 100% aman, hanya bisa diakses di Bangsa Bacol.\n"
        "</pre>"
        "🤔 <b>Sudah paham cara kerja Free Key?</b>"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Lanjut", callback_data="freekey_step1_yes")],
        [InlineKeyboardButton("❌ Batal", callback_data="freekey_cancel")]
    ])

    await message.reply(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)


# ================================
# CALLBACK HANDLER /freekey
# ================================
@app.on_callback_query(filters.regex(r"^freekey_"), group=1)
async def freekey_callback(client, cq: CallbackQuery):
    user_id = cq.from_user.id
    data = cq.data

    print(f"[CALLBACK DEBUG] user={user_id}, data={data}")

    if user_id not in FREEKEY_SESSIONS:
        await cq.answer("❌ Kamu belum mulai /freekey", show_alert=True)
        return

    if data == "freekey_cancel":
        del FREEKEY_SESSIONS[user_id]
        await cq.message.edit("🚪 Proses Free Key dibatalkan, sampai jumpa!", parse_mode=ParseMode.HTML)
        if LOG_CHANNEL_ID:
            try:
                await client.send_message(
                    LOG_CHANNEL_ID,
                    f"❌ {cq.from_user.mention} membatalkan /freekey"
                )
            except Exception:
                pass
        return

    if data == "freekey_step1_yes":
        FREEKEY_SESSIONS[user_id]["step"] = "STEP2_RULES"
        await cq.message.edit(
            "📜 <b>PERATURAN KOLEKSI FREE KEY</b>\n"
            "<pre>"
            "1. Wajib pakai <b>nama/username</b> (bisa julukan, samaran, atau medsos) sebagai nama koleksi.\n"
            "2. Selalu cek dulu di <b>/listvip</b> untuk menghindari duplikat.\n"
            "3. Jika ingin melengkapi koleksi yang sudah ada, gunakan nama koleksi yang sama.\n"
            "</pre>"
            "⛔ <b>Dilarang upload:</b>\n"
            "<pre>"
            "• Koleksi abal-abal / spam / OOT\n"
            "• Koleksi promosi (watermark, link, username)\n"
            "• Koleksi Deepfake, AI, non-Indo, atau semi/tidak nude\n"
            "• Koleksi hasil reupload dari Bangsa Bacol\n"
            "</pre>"
            "💡 <b>Catatan:</b>\n"
            "<pre>"
            "• Disarankan tidak upload koleksi tanpa wajah (anonim bisa pakai stiker atau filter wajah)\n"
            "• Koleksi yang kamu upload otomatis jadi milikmu di sistem\n"
            "• Koleksi terjaga (aman & tidak bisa diunduh)\n"
            "• Review bisa cepat/lama tergantung banyaknya antrian\n"
            "</pre>"
            "⚠️ <b>Pelanggaran = Peringatan / Ban sementara / Ban permanen</b>",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Setuju", callback_data="freekey_step2_yes")],
                [InlineKeyboardButton("❌ Tidak", callback_data="freekey_cancel")]
            ]),
            parse_mode=ParseMode.HTML
        )

    elif data == "freekey_step2_yes":
        FREEKEY_SESSIONS[user_id]["step"] = "COLLECT_MEDIA"
        await cq.message.edit(
            "┏━━━━━━━━━━━━━━━\n"
            "┃ 📤 <b>Kirim Media Koleksi</b>\n"
            "┗━━━━━━━━━━━━━━━\n\n"
            "Silakan kirim <b>foto / video / dokumen</b> sebanyak yang kamu mau.\n\n"
            "📌 Setelah selesai, tekan tombol <b>Selesai</b> di bawah ini.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Selesai", callback_data="freekey_finish")],
                [InlineKeyboardButton("❌ Batalkan", callback_data="freekey_cancel")]
            ]),
            parse_mode=ParseMode.HTML
        )

    elif data == "freekey_finish":
        if FREEKEY_SESSIONS[user_id]["count"] == 0:
            await cq.answer("❌ Belum ada media yang kamu kirim!", show_alert=True)
            return

        FREEKEY_SESSIONS[user_id]["step"] = "ASK_NAME"

        await cq.message.reply(
            f"✅ Koleksi berhasil terkumpul: <b>{FREEKEY_SESSIONS[user_id]['count']} media</b>.\n\n"
            "📝 Sekarang silakan <b>kirim nama koleksi</b> (ketik langsung di chat).\n"
            "Nama ini akan digunakan Admin untuk meninjau dan menampilkan koleksimu.\n\n"
            "👉 Kirim nama koleksi sekarang...",
            parse_mode=ParseMode.HTML
        )
    
    # ✅ Step konfirmasi nama asli
    elif data == "freekey_realname_yes":
        FREEKEY_SESSIONS[user_id]["is_real_name"] = True
        FREEKEY_SESSIONS[user_id]["step"] = "CONFIRM_PUBLISH"
        teks = (
            "┏━━━━━━━━━━━━━━━━━━━━━━\n"
            "┃ 📢 <b>PILIH MODE KOLEKSI</b>\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "👉 Mau koleksimu ditampilkan <b>Publik</b> atau tetap <b>Private</b>?\n\n"
            "🌍 <b>Publik</b> → Bisa tampil di <i>Bangsa Bacol</i>\n"
            "🔒 <b>Private</b> → Hanya barter key"
        )
        await cq.message.edit(
            teks,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🌍 Publish", callback_data="freekey_publish")],
                [InlineKeyboardButton("🔒 Private", callback_data="freekey_private")]
            ]),
            parse_mode=ParseMode.HTML
        )

    elif data == "freekey_realname_no":
        FREEKEY_SESSIONS[user_id]["is_real_name"] = False
        FREEKEY_SESSIONS[user_id]["step"] = "CONFIRM_PUBLISH"
        teks = (
            "┏━━━━━━━━━━━━━━━━━━━━━━\n"
            "┃ 📢 <b>PILIH MODE KOLEKSI</b>\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "👉 Mau koleksimu ditampilkan <b>Publik</b> atau tetap <b>Private</b>?\n\n"
            "🌍 <b>Publik</b> → Bisa tampil di <i>Bangsa Bacol</i>\n"
            "🔒 <b>Private</b> → Hanya bisa dipakai untuk barter key"
        )
        await cq.message.edit(
            teks,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🌍 Publish", callback_data="freekey_publish")],
                [InlineKeyboardButton("🔒 Private", callback_data="freekey_private")]
            ]),
            parse_mode=ParseMode.HTML
        )

    # ================================
    # Step Publish/Private → lanjut ke Kolpri
    # ================================
    elif data == "freekey_publish":
        FREEKEY_SESSIONS[user_id]["publish_mode"] = "PUBLISH"
        FREEKEY_SESSIONS[user_id]["step"] = "AFTER_PUBLISH1"
        await cq.message.edit(
            "┏━━━━━━━━━━━━━━━━━━━━━━\n"
            "┃ 👤 <b>KOLEKSI PRIBADI</b>\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "👉 Apakah ini <b>koleksi pribadi (kolpri)</b> milikmu sendiri?\n",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Ya, kolpri saya", callback_data="freekey_kolpri_yes")],
                [InlineKeyboardButton("❌ Tidak, bukan kolpri saya", callback_data="freekey_kolpri_no")]
            ]),
            parse_mode=ParseMode.HTML
        )

    elif data == "freekey_private":
        FREEKEY_SESSIONS[user_id]["publish_mode"] = "PRIVATE"
        FREEKEY_SESSIONS[user_id]["step"] = "AFTER_PUBLISH1"
        await cq.message.edit(
            "┏━━━━━━━━━━━━━━━━━━━━━━\n"
            "┃ 👤 <b>KOLEKSI PRIBADI</b>\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "👉 Apakah ini <b>koleksi pribadi (kolpri)</b> milikmu sendiri?\n",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Ya, kolpri saya", callback_data="freekey_kolpri_yes")],
                [InlineKeyboardButton("❌ Tidak, bukan kolpri saya", callback_data="freekey_kolpri_no")]
            ]),
            parse_mode=ParseMode.HTML
        )

    # ================================
    # Step AFTER_PUBLISH1 → user jawab kolpri
    # ================================
    elif data == "freekey_kolpri_yes":
        FREEKEY_SESSIONS[user_id]["is_kolpri"] = True
        FREEKEY_SESSIONS[user_id]["step"] = "AFTER_PUBLISH2"
        await cq.message.edit(
            "┏━━━━━━━━━━━━━━━━━━━━━━\n"
            "┃ 📝 <b>DESKRIPSI KOLEKSI</b>\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "👉 Tulis deskripsi atau pesan singkat untuk koleksimu.\n\n"
            "✍️ Kirim teks langsung di chat ini.",
            parse_mode=ParseMode.HTML
        )

    elif data == "freekey_kolpri_no":
        FREEKEY_SESSIONS[user_id]["is_kolpri"] = False
        FREEKEY_SESSIONS[user_id]["step"] = "AFTER_PUBLISH2"
        await cq.message.edit(
            "┏━━━━━━━━━━━━━━━━━━━━━━\n"
            "┃ 📝 <b>DESKRIPSI KOLEKSI</b>\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "👉 Tulis deskripsi atau pesan singkat untuk koleksimu.\n\n"
            "✍️ Kirim teks langsung di chat ini.",
            parse_mode=ParseMode.HTML
        )

    elif data == "freekey_social_yes":
        FREEKEY_SESSIONS[user_id]["step"] = "WAIT_SOCIAL_MEDIA"
        await cq.message.edit(
            "📸 Silakan kirim <b>screenshot username medsos</b> sekarang.\n\n"
            "Setelah kirim, sistem otomatis lanjut ke konfirmasi terakhir.",
            parse_mode=ParseMode.HTML
        )

    elif data == "freekey_social_no":
        FREEKEY_SESSIONS[user_id]["step"] = "AFTER_CONFIRM"

        deskripsi = FREEKEY_SESSIONS[user_id].get("deskripsi", "-")
        teks = (
            "┏━━━━━━━━━━━━━━━━━━━━━━\n"
            "┃ ✅ <b>KONFIRMASI TERAKHIR</b>\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📝 Deskripsi sudah dicatat:\n\n<code>{deskripsi}</code>\n\n"
            "👉 Apakah kamu ingin mengirim koleksi ini sekarang?"
        )

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Kirim", callback_data="freekey_confirm_send")],
            [InlineKeyboardButton("❌ Batal", callback_data="freekey_confirm_cancel")]
        ])

        await cq.message.edit(teks, reply_markup=kb, parse_mode=ParseMode.HTML)

# ================================
# HANDLE TEKS (Nama Koleksi + Deskripsi)
# ================================
@app.on_message(filters.private & filters.text, group=2)
async def freekey_text_handler(client, message: Message):
    user_id = message.from_user.id
    session = FREEKEY_SESSIONS.get(user_id)
    if not session:
        return

    # --- Step: Kirim nama koleksi ---
    if session.get("step") == "ASK_NAME":
        nama = (message.text or "").strip()
        session["nama"] = nama
        session["step"] = "CONFIRM_REALNAME"

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Asli", callback_data="freekey_realname_yes")],
            [InlineKeyboardButton("❌ Tidak", callback_data="freekey_realname_no")]
        ])

        await message.reply(
            "┏━━━━━━━━━━━━━━━━━━━━━━\n"
            "┃ 📝 <b>KONFIRMASI NAMA</b>\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📂 Nama koleksi kamu: <b>{nama}</b>\n\n"
            "❓ Apakah nama/username ini <b>asli</b>?",
            reply_markup=keyboard,
            parse_mode=ParseMode.HTML
        )
        return

    # --- Step: Kirim deskripsi koleksi ---
    if session.get("step") == "AFTER_PUBLISH2":
        deskripsi = message.text.strip()
        session["deskripsi"] = deskripsi
        session["step"] = "ASK_SOCIAL"

        teks = (
            "┏━━━━━━━━━━━━━━━━━━━━━━\n"
            "┃ 🔗 <b>VERIFIKASI KOLPRI</b>\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "👉 Jika memang ini adalah <b>Kolpri milikmu</b>, sebagai verifikasi kamu perlu "
            "mensubmit username/nama asli sosmed (IG/Telegram/Twitter, dsb — cukup salah satu). "
            "Ini untuk memastikan kolpri ini milikmu pribadi.\n\n"
            "✅ Keuntungan: Kolpri ini akan dipajang di /listvip, dan kamu akan mendapat 1 key "
            "setiap ada member yang unlock.\n\n"
            "🚫 Kamu bisa menolak verifikasi, dan tetap bisa publish, "
            "namun <b>tidak mendapat hak royalti</b> ketika kolpri kamu di-unlock member lain.\n\n"
            "⚠️ Verifikasi ini hanya untuk internal, tidak akan ikut dipublish "
            "(kecuali atas permintaan kamu).\n\n"
            "👉 Apakah kamu mau menambahkan <b>username medsos</b> sebagai verifikasi kolpri kamu?\n"
            "🌟 Jika Ya → lanjut kirim screenshot bukti username.\n"
            "🚫 Jika Tidak → langsung lanjut ke konfirmasi kirim."
        )

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Ya, saya akan kirim", callback_data="freekey_social_yes")],
            [InlineKeyboardButton("❌ Tidak, lanjut saja", callback_data="freekey_social_no")]
        ])

        await message.reply(teks, reply_markup=kb, parse_mode=ParseMode.HTML)
        return

# ================================
# Step AFTER_CONFIRM → konfirmasi kirim/batal
# ================================
import asyncio

@app.on_callback_query(filters.regex(r"^freekey_confirm_"), group=2)
async def cb_freekey_confirm(client, cq: CallbackQuery):
    user_id = cq.from_user.id
    session = FREEKEY_SESSIONS.get(user_id)
    print(f"[FREEKEY DEBUG] Callback confirm dipanggil. Step={session.get('step') if session else None}")

    if not session or session.get("step") != "AFTER_CONFIRM":
        return await cq.answer("⚠️ Tidak ada sesi aktif.", show_alert=True)

    action = cq.data.split("_")[-1]
    print(f"[FREEKEY DEBUG] Action={action}")

    if action == "send":
        await cq.answer("🚀 Koleksi sedang dikirim...", show_alert=False)

        stop_flag = asyncio.Event()

        async def animate_loading():
            dots = ["", ".", "..", "..."]
            i = 0
            while not stop_flag.is_set():
                try:
                    await cq.message.edit_text(
                        f"⏳ <b>Mengirim{dots[i]}</b>\n\n"
                        "Mohon tunggu sebentar sampai selesai ⏳",
                        parse_mode=ParseMode.HTML
                    )
                except Exception as e:
                    print(f"[FREEKEY WARN] Gagal update animasi: {e}")
                i = (i + 1) % len(dots)
                await asyncio.sleep(1)

        # Jalankan animasi di background
        animation_task = asyncio.create_task(animate_loading())

        try:
            print("[FREEKEY DEBUG] Memanggil finish_freekey() ...")
            await finish_freekey(client, cq.from_user, user_id)
            print("[FREEKEY DEBUG] finish_freekey() selesai tanpa error.")
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            print(f"[FREEKEY ERROR] finish_freekey gagal: {e}\n{tb}")
            await cq.answer(f"❌ Gagal kirim: {e}", show_alert=True)
        finally:
            stop_flag.set()  # hentikan animasi
            await animation_task

            # Hapus tombol biar tidak bisa diklik lagi
            try:
                await cq.message.edit_reply_markup(None)
            except Exception:
                pass

            FREEKEY_SESSIONS.pop(user_id, None)

    elif action == "cancel":
        try:
            await cq.message.edit_text(
                "❌ <b>Koleksi dibatalkan.</b>\n\n"
                "Kamu bisa mulai lagi dengan perintah <code>/freekey</code>.",
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            print(f"[FREEKEY WARN] Gagal edit pesan konfirmasi: {e}")

        await cq.answer("❌ Koleksi dibatalkan.", show_alert=False)
        FREEKEY_SESSIONS.pop(user_id, None)

# ================================
# HANDLE MEDIA (foto/video/dokumen/audio/voice)
# ================================
@app.on_message(
    filters.private & (filters.photo | filters.video | filters.document | filters.audio | filters.voice),
    group=1
)
async def freekey_handle_media(client, message: Message):
    user_id = message.from_user.id
    session = FREEKEY_SESSIONS.get(user_id)
    if not session:
        return

    # ✅ Step: user kirim screenshot medsos
    if session.get("step") == "WAIT_SOCIAL_MEDIA":
        try:
            sent: Message = await message.copy(CHANNEL_MEDIA)
            session["social_media_screenshot"] = sent.id
            session["step"] = "AFTER_CONFIRM"

            deskripsi = session.get("deskripsi", "-")
            teks = (
                "┏━━━━━━━━━━━━━━━━━━━━━━\n"
                "┃ ✅ <b>KONFIRMASI TERAKHIR</b>\n"
                "┗━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📝 Deskripsi sudah dicatat:\n\n<code>{deskripsi}</code>\n\n"
                "📸 Screenshot username medsos berhasil disimpan!\n\n"
                "👉 Apakah kamu ingin mengirim koleksi ini sekarang?"
            )

            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Kirim", callback_data="freekey_confirm_send")],
                [InlineKeyboardButton("❌ Batal", callback_data="freekey_confirm_cancel")]
            ])

            await message.reply(teks, reply_markup=kb, parse_mode=ParseMode.HTML)
        except Exception as e:
            await message.reply(f"❌ Gagal menyimpan screenshot: {e}")
        return

    # ✅ Step: kumpulkan media koleksi
    if session.get("step") != "COLLECT_MEDIA":
        return

    try:
        sent: Message = await message.copy(CHANNEL_MEDIA)
        session["media_ids"].append(sent.id)
        session["count"] = session.get("count", 0) + 1

        print(f"[FREEKEY DEBUG] User {user_id} kirim media. Total={session['count']} mid={sent.id}")

        await message.reply(
            "┏━━━━━━━━━━━━━━━━━━━━━━\n"
            "┃ ✅ <b>MEDIA DITERIMA</b>\n"
            "┗━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📥 Media ke-<b>{session['count']}</b> berhasil disimpan!\n"
            f"📦 Total terkumpul: <b>{session['count']} media</b>.\n\n"
            "👉 Mau lanjut tambah media atau sudah cukup?\n\n"
            "✅ Jika sudah, tekan tombol <b>Selesai</b> pada pesan sebelumnya.",
            parse_mode=ParseMode.HTML
        )

        # 🚩 log publik anonim
        data = load_user_data()
        user = data.get(str(user_id), {"badge": BADGE_STRANGER})
        await send_public_log(client, "freekey", badge=user.get("badge"))

        # log ke admin
        if LOG_CHANNEL_ID:
            await client.send_message(
                LOG_CHANNEL_ID,
                f"📸 {message.from_user.mention} mengirim media ke /freekey "
                f"(total: {session['count']})"
            )

    except Exception as e:
        await message.reply(f"❌ Gagal mengirim media: {e}")
        print(f"[FREEKEY ERROR] {e}")

# ================================
# FINISH FREEKEY
# ================================
async def finish_freekey(client, user, user_id: int):
    session = FREEKEY_SESSIONS.get(user_id)
    if not session:
        return

    user_info = session.get("user_info", {})
    username = user_info.get("username")
    first_name = user_info.get("first_name", "No Name")

    if username:
        user_text = f"@{username} (ID: <code>{user_id}</code>)"
    else:
        user_text = f"No Username / {first_name} (ID: <code>{user_id}</code>)"

    nama = session.get("nama")
    is_real = "Asli ✅" if session.get("is_real_name") else "Samaran ❌"
    publish = "🌍 Publish" if session.get("publish_mode") == "PUBLISH" else "🔒 Private"
    kolpri = "✅ Ya" if session.get("is_kolpri") else "❌ Tidak"
    deskripsi = session.get("deskripsi", "-")

    # ✅ cek screenshot medsos
    screenshot_id = session.get("social_media_screenshot")
    if screenshot_id:
        screenshot_text = "✅ Ada (screenshot dikirim)"
    else:
        screenshot_text = "❌ Tidak ada"

    # === kirim ke CHANNEL_MEDIA ===
    try:
        await client.send_message(
            CHANNEL_MEDIA,
            (
                "🆕 Koleksi baru masuk:\n"
                f"👤 Dari: {user_text}\n"
                f"📦 Nama Koleksi: {nama}\n"
                f"📸 Jumlah Media: {session['count']}\n"
                f"📝 Nama Asli: {is_real}\n"
                f"🔖 Mode: {publish}\n"
                f"👤 Kolpri: {kolpri}\n"
                f"📝 Deskripsi: {deskripsi}\n"
                f"🔗 Screenshot Medsos: {screenshot_text}\n\n"
                "⚠️ Silahkan Admin/Moderator meninjau media."
            ),
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        print(f"[FREEKEY ERROR] gagal kirim ke CHANNEL_MEDIA: {e}")

    # === kirim pesan konfirmasi ke user ===
    try:
        await client.send_message(
            user_id,
            "┏━━━━━━━━━━━━━━━\n"
            "┃ ✅ <b>Media Berhasil Dikirim</b>\n"
            "┗━━━━━━━━━━━━━━━\n\n"
            "Terima kasih! Koleksimu sudah masuk ke sistem dan sedang dalam proses <b>tinjauan Admin</b>.\n\n"
            "🔎 <b>Proses Tinjauan:</b>\n"
            "<pre>"
            "• Admin akan mengecek kualitas & kelayakan koleksi.\n"
            "• Jika lolos, kamu akan menerima <b>reward berupa KEY</b> sesuai jumlah & kualitas.\n"
            "• Koleksi yang dipilih bisa ditampilkan di <b>/listvip</b> agar member lain bisa unlock.\n"
            "</pre>"
            "💡 <b>Catatan:</b>\n"
            "<pre>"
            "• Koleksi private tetap dihitung reward-nya meski tidak tampil publik.\n"
            "• Semakin unik & lengkap koleksi, semakin besar peluang dapat bonus Key.\n"
            "• Harap sabar menunggu, proses bisa memakan waktu tergantung antrian.\n"
            "</pre>"
            "🙏 Terima kasih sudah berkontribusi ke <b>Bangsa Bacol</b>!\n"
            "✨ Koleksimu membantu komunitas makin berkembang ✨",
            parse_mode=ParseMode.HTML
        )
    except Exception as e:
        print(f"[FREEKEY ERROR] gagal kirim pesan ke user: {e}")

    # === log admin ===
    if LOG_CHANNEL_ID:
        try:
            await client.send_message(
                LOG_CHANNEL_ID,
                (
                    f"✅ {user_text} selesai submit /freekey\n"
                    f"📦 Nama Koleksi: {nama}\n"
                    f"📸 Jumlah Media: {session.get('count')}\n"
                    f"📝 Nama Asli: {is_real}\n"
                    f"🔖 Mode: {publish}"
                ),
                parse_mode=ParseMode.HTML
            )
        except Exception as e:
            print(f"[FREEKEY ERROR] gagal kirim log admin: {e}")

    # === bersihkan session ===
    FREEKEY_SESSIONS.pop(user_id, None)

# =====================================================
# PRIORITAS GROUP
# =====================================================
# group=0 : admin session utama (giftkey, topup, resetkey, key, qris, vip join)
# group=1 : freeKey (upload media, callback)
# group=2 : collectvip (media handler)
# group=3 : collectvip step teks
# group=99: fallback unknown
# =====================================================

# ================================
# SESSION ROUTER
# ================================
@app.on_message(
    filters.private & filters.text & ~filters.command([
        "topup", "resetkey", "key", "giftkey", "qris", "cancelgift",
        "collectvip", "abort_collect", "finish_collect",
        "addmedia", "abort_addmedia", "finish_addmedia",
    ]),
    group=0
)
async def session_router(client, message: Message):
    user_id = message.from_user.id

    # 🚫 Jangan ganggu session collectvip / addmedia
    if user_id in COLLECT_STEPS or user_id in ADD_MEDIA_SESSIONS:
        return

    print(f"[ROUTER DEBUG] masuk session_router user={user_id}, text={message.text}")

    # --- GIFTKEY ---
    if user_id in GIFT_SESSIONS:
        print(f"[ROUTER DEBUG] Gift session aktif untuk {user_id}")
        await gift_session_handler(client, message)
        await message.stop_propagation()
        return

    # --- TOPUP ---
    if user_id in TOPUP_SESSIONS:
        print(f"[ROUTER DEBUG] Topup session aktif untuk {user_id}")
        await topup_session_handler(client, message)
        await message.stop_propagation()
        return

    # --- RESETKEY ---
    if user_id in RESET_SESSIONS:
        await resetkey_session_handler(client, message)
        await message.stop_propagation()
        return

    # --- KEY ---
    if user_id in KEY_SESSIONS:
        await key_session_handler(client, message)
        await message.stop_propagation()
        return

    # --- QRIS ---
    if user_id in QRIS_SESSIONS:
        await qris_session_handler(client, message)
        await message.stop_propagation()
        return

    # --- VIP JOIN ---
    if user_id in VIP_SESSIONS:
        await cb_joinvip_saweria(client, message)
        await cb_joinvip_trakteer(client, message)
        await message.stop_propagation()
        return

# ================================
# Unknown Command & Fallback
# ================================
@app.on_message(filters.private, group=99)
async def unknown_or_fallback(client, message: Message):
    user_id = message.from_user.id

    # 🚩 Tangani FreeKey ASK_NAME di sini
    if user_id in FREEKEY_SESSIONS and FREEKEY_SESSIONS[user_id].get("step") == "ASK_NAME":
        print(f"[FALLBACK DEBUG] handle_freekey_name untuk {user_id}: {message.text}")
        await handle_freekey_name(client, message)
        await message.stop_propagation()
        return

    # 🚩 Kalau sedang di sesi lain, hentikan propagation
    if (
        user_id in COLLECT_STEPS
        or user_id in ADD_MEDIA_SESSIONS
        or user_id in waiting_lapor_users
        or user_id in waiting_feedback_users
        or user_id in TOPUP_SESSIONS
        or user_id in KEY_SESSIONS
        or user_id in QRIS_SESSIONS
        or user_id in GIFT_SESSIONS
        or user_id in VIP_SESSIONS
        or (user_id in FREEKEY_SESSIONS and FREEKEY_SESSIONS[user_id].get("step") != "ASK_NAME")
    ):
        await message.stop_propagation()
        return

    # 🚩 Command tidak dikenal
    if message.text and message.text.startswith("/"):
        cmd = message.text.split()[0][1:]
        known_cmds = {
            # umum
            "start", "help", "topup", "resetkey", "key", "lapor", "feedback", "setvip",
            "myvip", "profile", "random", "top", "panduan", "ping", "joinvip", "unsetvip",
            "about", "canceltopup", "request", "bot", "search", "free", "listvip", "claimbio",
            "stats", "log", "dashboard", "cancelgift", "healthcheck", "claim", "whois",
            "freekey", "giftkey", "hasil_request", "mute", "unmute", "batal", "qris",
            "badwords",
            # VIP Management
            "addvip", "delvip", "add", "delete", "prune_logs", "setowner",
            "reload_badwords", "reload_interaction", "reset_top", "unsetowner",
            # File collect
            "collectvip", "abort_collect", "finish_collect", "reset_collect", "delcollect",
            "addmedia", "abort_addmedia", "finish_addmedia"
        }

        if cmd not in known_cmds:
            await message.reply_text(
                f"⚠️ Command <code>/{cmd}</code> tidak dikenali.\n"
                f"Coba cek ulang command kamu.",
                parse_mode=ParseMode.HTML
            )
        await message.stop_propagation()
        return

    # 🚩 Kalau bukan command → fallback teks umum
    teks = f"""
🤖 <b>Hmmm...</b> aku nggak paham maksudmu wahai manusia.

Jika butuh bantuan coba cek ini:
📜 Daftar Bantuan → <a href="https://t.me/BangsaBacol/8">Klik di sini</a>  
📩 Lapor ke Admin-Pusat → <a href="https://t.me/BangsaBacol_Bot?start=lapor">Klik di sini</a>   
"""
    await message.reply_text(
        teks,
        disable_web_page_preview=True,
        parse_mode=ParseMode.HTML
    )
    await message.stop_propagation()

@app.on_callback_query()
async def debug_all_callback(client, cq: CallbackQuery):
    print(f"[DEBUG CALLBACK] data={cq.data} from={cq.from_user.id}")

# ================================
# Background Tasks
# ================================

# --- Periodic Message Task ---
async def send_periodic_message():
    logger.info("Periodic message task started!")
    while True:
        try:
            logger.info("Periodic message loop tick!")  # <--- Tambahkan ini
            if INTERACTION_MESSAGES:
                msg = random.choice(INTERACTION_MESSAGES)
                logger.info(f"Periodic message: {msg}")
                await app.send_message(chat_id=f"@{GROUP_USERNAME}", text=msg)
        except Exception as e:
            logger.error(f"Gagal kirim pesan periodik: {e}")
        await asyncio.sleep(INTERACTION_INTERVAL_MINUTES * 60)


async def periodic_log_prune():
    await asyncio.sleep(30)
    while True:
        try:
            prune_clicks_log()
            logger.info(f"Pruned clicks.jsonl (retention {RETENTION_DAYS} hari)")
        except Exception as e:
            logger.error(f"Gagal prune clicks.jsonl: {e}")
        await asyncio.sleep(24 * 3600)

# ================================
# Main
# ================================

if __name__ == "__main__":
    load_vip_map()
    load_stream_map()
    load_badwords_config()
    load_interaction_config()
    try:
        app.start()
        logger.info("🚀 BOT AKTIF ✅ @BangsaBacolBot")
        
        # Tambahkan periodic tasks ke event loop milik app
        app.loop.create_task(send_periodic_message())
        app.loop.create_task(periodic_log_prune())
        app.loop.create_task(fake_log_task(app))

        app.loop.run_forever()
    except KeyboardInterrupt:
        logger.info("👋 Bot dimatikan. Sampai jumpa!")
    except Exception as e:
        logger.error(f"Terjadi kesalahan fatal saat menjalankan bot: {e}")
    finally:
        app.stop()

