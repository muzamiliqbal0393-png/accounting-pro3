# Deploying ACCOUNTING PRO

The app is a Flask + SQLite application. That single fact decides where it can
run **well**, so read the short table first, then follow the option you want.

| Host | Works? | Data survives a restart? | Verdict |
|---|---|---|---|
| **Render** (web service + 1 GB disk) | Yes | **Yes** — database and uploads live on the disk | **Recommended.** Real accounting use. |
| **Railway / Fly.io / VPS / PythonAnywhere** | Yes | Yes, with a mounted volume | Equally fine. Same `Procfile` / `gunicorn` line. |
| **Vercel** (serverless functions) | Yes, it boots and every screen works | **No** — Vercel's filesystem is read-only except `/tmp`, and `/tmp` is per-instance and wiped | **Demo / showcase only.** Great for a public link, not for storing your 24 transactions. |

Vercel is genuinely serverless: each request may hit a different instance, each
instance has its own `/tmp`, and instances are recycled. No amount of code can
make a SQLite *file* persistent there — that is a platform limit, not a bug in
this app. (A hosted Postgres or Turso/libSQL database would be the Vercel-native
answer, and that would break the "SQLite" requirement of the project.)

The app ships with support for both shapes:

* **Normal mode** (local, Render, VPS): the database is `accounting_pro.db` next
  to the code, uploads go to `uploads/`.
* **Serverless mode** (`VERCEL=1` is set automatically by Vercel): `config.py`
  switches the database and the upload folders to `$TMPDIR/accounting-pro-data`,
  and copies the bundled `accounting_pro.db` there on the first request of each
  instance so the deployed app opens with the demo business already loaded.
  Override the location with `ACCOUNTING_PRO_DATA_DIR` and
  `ACCOUNTING_PRO_UPLOAD_DIR` if you want.

---

## Option A — Vercel (public demo link, ~3 minutes)

Files already in the repo: `api/index.py` (WSGI entry point), `vercel.json`
(runtime + `includeFiles` so templates, static files and the demo database are
bundled), `.vercelignore`.

1. Push the project to GitHub (from the project folder):

   ```bash
   git init
   git add .
   git commit -m "ACCOUNTING PRO - complete computerized accounting system"
   git branch -M main
   git remote add origin https://github.com/<your-user>/accounting-pro.git
   git push -u origin main
   ```

2. vercel.com → **Add New… → Project → Import Git Repository** → pick
   `accounting-pro`. Framework preset: **Other** (leave build/output empty —
   `vercel.json` handles it). Deploy.

3. In **Project → Settings → Environment Variables** add, then redeploy:

   | Key | Value |
   |---|---|
   | `SECRET_KEY` | a long random string (see below) — **required**: sessions and CSRF tokens are signed with it |
   | `ACCOUNTING_PRO_SEED_FROM_BUNDLE` | `1` (default) to open with demo data, `0` to open empty |

   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

4. Open the URL and sign in with the demo user
   `demo@accountingpro.local` / `Demo12345`, or register a fresh account.

Things to know on Vercel:

* Anything you post (voucher, adjustment, import, restore) is written to that
  instance's `/tmp` copy and can disappear on the next cold start. Treat it as a
  read-mostly demo — perfect for showing the examiner, wrong for entering the 24
  college transactions you need to keep.
* Request bodies are capped at **4.5 MB** by the platform (the app's own limit
  is 8 MB), so very large imports/uploads can be rejected by Vercel before Flask
  sees them.
* PDF/Word/Excel exports work and return normally (they are streamed from the
  database, nothing is written to disk except a temp buffer).
* Cold starts take ~1–2 s while pandas/reportlab import.

---

## Option B — Render with a persistent disk (recommended for real use)

`render.yaml` is included, so this is a blueprint deploy:

1. Push the project to GitHub (same commands as above).
2. render.com → **New + → Blueprint** → select the repo → **Apply**.
3. Render creates the web service, generates `SECRET_KEY`, mounts a 1 GB disk at
   `/var/data` and sets `ACCOUNTING_PRO_DATA_DIR=/var/data`, so
   `/var/data/accounting_pro.db` and `/var/data/uploads/` persist across deploys
   and restarts. First boot copies the bundled demo database onto the disk; if
   you want a clean start instead, launch once, then use
   **Backup & Restore → restore** with an empty database or delete the file from
   the Render shell (`rm /var/data/accounting_pro.db`) and restart.
4. Free instances sleep after inactivity; the **Starter** plan is required for
   disks. `Procfile` contains the same start command for hosts that read it.

Notes: run **one** web process (`--workers 1`), because SQLite allows a single
writer; the threads handle concurrency. For heavier use, move to Postgres.

---

## Option C — Railway / Fly.io / VPS / PythonAnywhere

Same as Render: install `requirements.txt`, set `SECRET_KEY`, mount a volume,
point `ACCOUNTING_PRO_DATA_DIR` at it, and start with

```bash
gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 8 --timeout 120
```

On PythonAnywhere, use the WSGI file with `from app import app as application`
and keep the project in `/home/<user>/accounting_pro`.

---

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `SECRET_KEY` | dev placeholder | **Set this in production.** Signs the session cookie and CSRF tokens. Must be identical across processes. |
| `ACCOUNTING_PRO_DATA_DIR` | project folder, or `$TMPDIR/accounting-pro-data` on serverless | Where `accounting_pro.db` and `uploads/` live. |
| `ACCOUNTING_PRO_UPLOAD_DIR` | `<data dir>/uploads` | Logo, attachment, import and backup files. |
| `ACCOUNTING_PRO_SEED_FROM_BUNDLE` | `1` | Copy the bundled demo database on first boot. `0` = start empty. |
| `DATABASE_URL` | `sqlite:///<data dir>/accounting_pro.db` | Full SQLAlchemy URL; set it if you ever move to another database. |

## Pre-flight checks before you push

```bash
pip install -r requirements.txt
python3 -m pytest -q          # 53 passed
python3 app.py                # http://127.0.0.1:5000

# simulate the serverless layout (read-only bundle)
VERCEL=1 ACCOUNTING_PRO_DATA_DIR=/tmp/ap-test python3 -c "
from api.index import app
print(app.config['SQLALCHEMY_DATABASE_URI'])"
```
