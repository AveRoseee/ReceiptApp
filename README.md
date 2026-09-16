# DokumenUsaha

Aplikasi Windows offline untuk penawaran, invoice, DP/cicilan dan kwitansi
bisnis jasa Indonesia. Python + Flet + SQLite; satu pengguna dan satu usaha
per database.

## Status

Environment awal tersedia. Fondasi database v0.2 ditambahkan, dengan migrasi
atomik, constraint, riwayat dan saldo pembayaran turunan. Modul bisnis serta
tampilan operasional belum dibuat; `main.py` masih layar uji awal.

- [Konteks dan keputusan dari percakapan sebelumnya](docs/PROJECT_CONTEXT.md)
- [Desain fisik database dan pekerjaan berikutnya](docs/DATABASE_DESIGN.md)
- [Schema awal](app/database/migrations/001_initial_schema.sql)

## Menjalankan

Dari folder proyek di PowerShell, environment yang sudah ada bernama `venv`:

```powershell
.\venv\Scripts\python.exe main.py
```

Inisialisasi database pengembangan secara terpisah:

```powershell
.\venv\Scripts\python.exe -m app.database --path .\data\dokumenusaha.db
```

Tanpa `--path`, lokasi default adalah direktori data pengguna Windows yang
ditentukan platformdirs. Database bukan disimpan di folder instalasi program.
Inisialisasi belum dipanggil otomatis oleh layar uji Flet.

## Pengujian

```powershell
.\venv\Scripts\python.exe -m pytest -q
```

Tes memakai database sementara, bukan data usaha pengguna.
`requirements.txt` yang sudah ada dipertahankan sebagai snapshot dependency
environment. Tidak ada server aplikasi yang perlu disiapkan untuk produk v1.

