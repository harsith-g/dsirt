# Deploying the fixed exam server (VPS KVM 4)

The live server is the **Node server** (`server.ts` → `dist/server.cjs`).
The Python `backend/` folder is the old version and is not used.

## 1. Update the code on the VPS

```bash
pm2 stop all            # or stop however you run it today
cd /path/to/dsirtphd-main
# copy the new files over the old ones (keep your own .env)
npm install
npm run build
cp .env.example .env    # only the first time — then edit .env
nano .env               # set EXAM_SECRET_KEY, ADMIN_PASSWORD, timings
```

## 2. Start it — ONE process only

```bash
pm2 delete all          # removes any old cluster / duplicate processes
pm2 start ecosystem.config.cjs
pm2 save
pm2 logs phd-exam --lines 30
```

You should see:

```
[db] Question bank: 550 questions loaded, 0 rejected.
Exam Portal Server running on http://0.0.0.0:3000
[config] Phase 1: 60 min · Break: 15 min · Phase 2: 60 min · exam_open=false
```

**Never** run it with `pm2 -i max`, `exec_mode: cluster`, or two copies at once.
Each copy would have its own exams — that is exactly what caused "can't log in",
"question jumps" and "auto-submit" before. The server now refuses to run a second copy.

## 3. nginx + HTTPS

Use `deploy/nginx-exam.conf`. The two WebSocket lines (`Upgrade` / `Connection`)
are required, otherwise the admin sees no camera. HTTPS is required, otherwise
phones and laptops block the camera.

Check: open `https://your-domain/healthz` → `{"ok":true,...}`.

## 4. On exam day

1. Admin signs in → **Start Exam for All** (the exam starts closed after a fresh install).
2. Candidates sign in with their email + `dsirt@123` → Start Phase 1.
3. After Phase 1 (submit or time-out) each candidate gets a 15-minute break and is signed out.
4. After the break they sign in again → Start Phase 2 → choose domain.
5. Admin can: watch the live grid (1 frame/s) or click a candidate for smooth video (~5 fps),
   **Disqualify**, **Re-open** (continues with remaining time), **Open P2 now** (skip break),
   **End Exam for All**.

## 5. Data & restarts

* Everything is saved every 2 seconds to `data/runtime_state.json` and on every submit.
  Restarting the server (or the VPS) during the exam loses nothing — candidates just
  sign in again and continue with the same time.
* **Back up** `data/runtime_state.json` after the exam.
* To start completely fresh (e.g. after a mock test): `pm2 stop phd-exam`,
  move `data/runtime_state.json` away, `pm2 start phd-exam`.
* Changing questions: edit `data/master_questions.json` and restart — or use the
  Question Bank tab (those edits are kept in `runtime_state.json`).

## 6. Test results before delivery

* 100 candidates at once (login, start, 50 answers each, camera on, submit):
  0 errors, slowest request 35 ms, every answer stored on the correct question.
* 250 candidates at once: 0 errors, slowest request 205 ms.
* Browser tests on desktop, iPhone-size and small Android screens: full Phase 1 →
  time-out → break → re-login → Phase 2 → finish, reload mid-exam, rapid Next taps.
