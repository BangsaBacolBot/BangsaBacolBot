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


📌 Lisensi internal → hanya untuk project Bangsa Bacol, bukan untuk publik.
