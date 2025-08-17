📖 Bangsa Bacol Bot

Bot Telegram untuk mengelola & memberi akses ke koleksi streaming.
Dibuat dengan Pyrogram, Python 3.

🚀 Fitur Utama

/start <kode> → Buka koleksi (validasi join channel & group dulu)

/list → Daftar semua koleksi (owner only)

/stats → Statistik klik 7 hari terakhir (owner only)

/dashboard → Dashboard interaktif (periode 24h/7d/30d) (owner only)

/search <keyword> → Cari kode koleksi (owner only)

/add <kode> <link> [thumb] → Tambah/Update koleksi (owner only)

/delete <kode> → Hapus koleksi (owner only)

/healthcheck → Cek status semua URL streaming (owner only)

/ping → Cek status bot

/about → Info bot

Auto greet member baru di group

Auto prune logs (retensi klik 7 hari)

Periodic message di group tiap 12 jam

📂 Struktur File
.
├── main.py              # File utama bot
├── stream_links.json    # Database kode → link
├── .env                 # Konfigurasi rahasia
├── logs/                # Folder logs
│   ├── bot_activity.log
│   ├── clicks.jsonl
│   └── health_check.log
└── Img/                 # Folder gambar (terkunci, thumbnail, dll.)

⚙️ Konfigurasi .env

Buat file .env di root project:

API_ID=123456
API_HASH=xxxxxxxxxxxxxxxxxxxxxx
BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
CHANNEL_USERNAME=BangsaBacol
GROUP_USERNAME=BANGSABACOLGROUP
OWNER_ID=123456789

▶️ Cara Menjalankan

Clone repo atau copy project

Install dependency:

pip install -r requirements.txt


(isi requirements.txt bisa berisi: pyrogram tgcrypto python-dotenv aiohttp)

Jalankan bot:

python main.py


Bot akan aktif dan menulis log ke logs/

📊 Logging

Semua aktivitas → logs/bot_activity.log

Semua klik koleksi → logs/clicks.jsonl

Healthcheck → logs/health_check.log

Klik otomatis dipangkas setiap 7 hari (retensi = 7 hari)

🔐 Command Owner Only

/list

/stats

/dashboard

/search

/add

/delete

/healthcheck

Pastikan OWNER_ID di .env sesuai dengan user Telegrammu.

💡 Tips Pengembangan

Kalau mau deploy di VPS, gunakan pm2 atau systemd agar auto restart.

Kalau mau deploy di Heroku / Railway, pastikan environment variable sudah diisi.

Kalau user makin banyak, bisa pertimbangkan migrasi log ke SQLite biar lebih kuat dibanding file JSONL.

📌 Lisensi internal → hanya untuk project Bangsa Bacol, bukan untuk publik.
