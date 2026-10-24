# Production Deployment — Hostinger KVM 4 VPS

Target box: **62.72.29.111** / **srv1990910.hstgr.cloud** — 4 vCPU / 16 GB RAM.

> **I cannot run any of this for you.** This sandbox has no SSH access or
> device-bridge connection to your VPS, so everything below is a runbook to
> execute yourself (or hand to whoever has root on that box), not something
> already applied. Nothing on the live server has been touched.

## What's in this folder

| File | Purpose |
|---|---|
| `gunicorn.conf.py` | App server config — **read the `workers = 1` comment block before changing it.** It's deliberate, not a mistake; see below. |
| `nginx.conf` | Reverse proxy: terminates HTTP(S), forwards to gunicorn on `127.0.0.1:8000`, with full WebSocket support for live proctoring. |
| `phd-exam.service` | systemd unit — `Restart=always` so the app comes back on its own after a crash, OOM kill, or reboot. |

## One important deviation from a "standard" 4-core sizing

A textbook `2 x vCPU + 1` sizing on this 4-core box would suggest 9 gunicorn
workers. **This deployment intentionally runs 1.** `backend/websocket_manager.py`
keeps every candidate's and admin's WebSocket connection in an in-memory,
single-process registry. Gunicorn workers are separate OS processes — a
candidate's camera-frame WebSocket and an admin's dashboard WebSocket landing
on *different* worker processes would mean that admin never sees that
candidate's live camera feed, silently. With 9 workers and 100+ candidates,
most admin sessions would see most cameras as permanently offline. Full
reasoning and the upgrade path (Redis Pub/Sub) is in the comment block at the
top of `gunicorn.conf.py` — read it before touching `workers`.

A single asyncio worker is genuinely enough for 100-200 concurrent candidates
on this workload (small JSON API calls + a steady trickle of already-compressed
JPEG frames); the other 3 vCPUs are free for PostgreSQL, nginx, and OS overhead.

## First-time setup, step by step

Run these as root (or via `sudo`) on the VPS itself.

### 1. System packages

```bash
apt-get update
apt-get install -y python3-venv python3-pip postgresql postgresql-contrib nginx git
```

### 2. PostgreSQL

```bash
sudo -u postgres psql -c "CREATE USER phdexam WITH PASSWORD 'CHANGE_ME';"
sudo -u postgres psql -c "CREATE DATABASE phd_exam OWNER phdexam;"
```

### 3. Application user + code

```bash
useradd --system --home /var/www/phd-exam --shell /usr/sbin/nologin phdexam
mkdir -p /var/www/phd-exam
git clone https://github.com/kevin11-afk/dsirtphd.git /var/www/phd-exam
cd /var/www/phd-exam

python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

### 4. Environment

Create `/var/www/phd-exam/.env` (copy `.env.example` as a starting point) —
this is what `phd-exam.service`'s `EnvironmentFile=` loads:

```ini
EXAM_SECRET_KEY=<generate with: python3 -c "import secrets; print(secrets.token_hex(32))">
EXAM_DATABASE_URL=postgresql://phdexam:CHANGE_ME@localhost:5432/phd_exam
ADMIN_EMAIL=admin@yourdomain.edu
ADMIN_PASSWORD=<a strong password — this is the real production admin login>
CANDIDATE_DEFAULT_PASSWORD=<the shared password issued to candidates>
ALLOWED_ORIGINS=http://62.72.29.111,http://srv1990910.hstgr.cloud
```

Once HTTPS is live (step 8), update `ALLOWED_ORIGINS` to the `https://` origin
and restart the service.

### 5. Database schema — Alembic, not create_all()

```bash
cd /var/www/phd-exam
./venv/bin/alembic upgrade head
```

This is a **brand-new** database, so `upgrade head` is correct. (If you were
ever migrating a pre-existing database that an older version of this app had
already populated via `Base.metadata.create_all()`, you'd use
`alembic stamp head` instead of `upgrade head` for that one-time cutover —
see the docstring in `migrations/versions/0001_initial_schema.py`. Not your
case on a fresh VPS.)

### 6. Question bank

Log in as admin once the app is running (step 7) and upload
`data/question_bank/question_bank.zip` from **Admin Dashboard → Question
Bank → Upload**. See `data/question_bank/README.md` for exactly what's in it
(448 questions across 9 domains; Commerce & Management and 2 Computer
Science questions are excluded — no answer key in the source data).

### 7. systemd service

```bash
cp /var/www/phd-exam/deploy/phd-exam.service /etc/systemd/system/phd-exam.service
systemctl daemon-reload
systemctl enable --now phd-exam
systemctl status phd-exam          # should show "active (running)"
curl -s http://127.0.0.1:8000/healthz   # should return {"status": "ok"}
```

### 8. nginx + HTTPS

```bash
cp /var/www/phd-exam/deploy/nginx.conf /etc/nginx/sites-available/phd-exam.conf
ln -s /etc/nginx/sites-available/phd-exam.conf /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx

# Optional but strongly recommended — live camera proctoring sends candidate
# video over the network; that traffic should be encrypted:
apt-get install -y certbot python3-certbot-nginx
certbot --nginx -d srv1990910.hstgr.cloud
```

`certbot` rewrites `/etc/nginx/sites-available/phd-exam.conf` in place to add
the HTTPS server block and an HTTP→HTTPS redirect — that's expected and
correct; don't copy the plain `deploy/nginx.conf` back over it afterwards.

### 9. Smoke-test

```bash
ADMIN_PASSWORD=<your ADMIN_PASSWORD> ./smoke_test.sh
```

Note `smoke_test.sh` targets `http://localhost:8811` by default — either
edit the `BASE=` line at the top of the script to `http://127.0.0.1:8000` (or
`https://srv1990910.hstgr.cloud`), or run gunicorn once ad hoc on `:8811`
before cutting over. It was not re-pointed automatically as part of this PR
since it's a local dev convenience script, not part of the deploy path.

## Redeploying after a code change

```bash
cd /var/www/phd-exam
git pull
./venv/bin/pip install -r requirements.txt
./venv/bin/alembic upgrade head
systemctl restart phd-exam
```

## Operational notes

- **Logs**: `journalctl -u phd-exam -f` (app/gunicorn), `/var/log/nginx/phd-exam.*.log` (nginx).
- **Camera snapshot storage**: `EXAM_UPLOADS_DIR` (default `./uploads/proctor_streams`
  relative to the working directory) fills up over a long exam — it's a
  periodic JPEG audit snapshot per candidate, not the full stream. Monitor
  disk usage (`df -h`) during/after a live exam and archive or prune old
  session folders between exam sittings.
- **Backups**: this runbook doesn't set up automated PostgreSQL backups —
  do that separately (`pg_dump` on a cron, or Hostinger's own snapshot
  feature) before running a real cohort through this.
