# Ph.D. Entrance Examination & Live Proctoring Portal

Two-phase entrance examination with server-authoritative timing, live WebRTC
proctoring and a bulk question-bank importer. FastAPI + PostgreSQL 16 + Redis 7
+ Coturn + Nginx, all in Docker Compose. Yellow-and-white interface, fully
responsive, zero external CDNs.

---

## Read this first: Tamil needs its answer key

**450 of 500 questions import.** Nine of the ten papers are complete at 50/50.
One gap remains.

| Paper | Status | What is needed |
|---|---|---|
| **Tamil** | Script is **fully recovered** — 3,971 correct Tamil characters, renders properly. But **all 50 `correct_answer` cells are empty**; every row ends in a trailing comma. Readable, but unanswerable. | Fill the `correct_answer` column (A/B/C/D), then `make reseed` — or upload through **Admin → Question bank** (phase *Phase 2*, domain *TAM*). |

**Tamil is seeded as a real domain but `is_active = false`**, so candidates
cannot choose a domain with no paper behind it. It becomes selectable the
moment its questions import — no code change, no restart beyond the import.

Computer Science Q38 and Q39 are now keyed to **B** (*Encoder*, and *"Output of
a combinational circuit depends only on current inputs"*) on your instruction.
That is the only manual entry anywhere in the bank, and it is recorded in
`backend/seed_data/NOTES.md` with its authority and date. Nothing else has been
inferred: the importer withholds any question it cannot key from the source and
names it.

### What imports today

| Paper | Phase / domain | Imported |
|---|---|---:|
| `DSU_Research_Methodology_Set_II-_1_.csv` | **Phase 1** (common) | 50 |
| `LIF-SCIENCE.csv` | Phase 2 · `LIFE` | 50 |
| `Pathology.csv` | Phase 2 · `PATH` | 50 |
| `entomology.csv` | Phase 2 · `ENTO` | 50 |
| `PhD- Entrance  Eng QP.csv` | Phase 2 · `ENG` | 50 |
| `COMPUTER-SCIENCE-ENTRANCE-TEST-QUESTIONS-_1_.csv` | Phase 2 · `CS` | 50 |
| `MATHS.csv` | Phase 2 · `MATH` | 50 |
| `AGRONOMY-Final.csv` | Phase 2 · `AGRO` | 50 |
| `PhD_Entrance_Exam_Commerce_Management_Alternate_Questions.csv` | Phase 2 · `COMM` | 50 |
| `Ph.D-ENTRANCE-EXAM-TAMILTAMILQuestion-_1_.csv` | Phase 2 · `TAM` | **0** — no answer key |
| | | **450** |

`make inspect` reprints this against the current files at any time.

---

## Quick start

```bash
cp .env.example .env          # then edit SECRET_KEY and the passwords
make up                       # builds, migrates, seeds and serves
make logs                     # watch the seed report
```

| | |
|---|---|
| Candidate login | <http://localhost:8080/login> |
| Admin login | <http://localhost:8080/admin/login> |
| Admin account | `admin@examportal.edu` / `Admin@123` |
| Candidate password | `dsirt@123` (50 accounts, all `APPROVED`) |
| API docs | <http://localhost:8080/api/docs> (off when `ENVIRONMENT=production`) |
| Health | <http://localhost:8080/health> |

`make help` lists every target.

---

## Your logo

**`logo.png` was not in the bundle**, so the portal ships with a neutral
placeholder mark that belongs to no institution.

To use your own: drop **`logo.png`** into `backend/app/static/img/` and run
`docker compose restart backend`. It then appears on the candidate sign-in
page, the admin sign-in page, the examination header and every dashboard
header. No template or code change is needed — the backend resolves
`logo.png` → `logo.svg` → placeholder at startup.

Recommended: transparent PNG, landscape, at least 240 × 72 px (480 × 144 for
high-DPI). See `backend/app/static/img/README.md`.

---

## Interface

Crisp white surfaces, warm yellow (`#f5b301`) for primary actions and accents,
near-black warm grey type. Yellow is used for emphasis and action only — it is
a poor text colour and a poor "success" colour, so status still reads green /
amber / red, and text on a yellow background is always near-black, never white.

**Nothing loads from a CDN.** All CSS and JS is served by the application, so
the exam hall works under restricted egress. There is no build step.

Responsive behaviour, verified by screenshotting every page at 360, 390, 430,
768, 834 and 1440 px:

- **Phones** — single column; the **question comes first**, before the palette,
  so a candidate never scrolls past 50 buttons to reach what they are
  answering. Previous / Mark / Next sit in one sticky, thumb-reachable bar.
  Nav scrolls horizontally. Stat tiles go two-up.
- **The self-view docks top-right on phones**, not bottom-right. Anchoring it
  above the sticky action bar cannot be made safe — that bar sits anywhere
  between its natural position and the viewport floor depending on scroll, so
  any fixed bottom offset overlaps it in some scroll state. The header is
  pinned at `top: 0`, so it is the one stable anchor; `exam.js` measures the
  real header height at runtime and publishes it as `--header-h`.
- **Tablets** — the proctoring rail collapses under the feed grid; the grid
  reflows from four columns to two to one.
- **Touch** — every control is at least 44 px (WCAG 2.5.5 / iOS HIG), inputs
  are 16 px so iOS Safari does not zoom on focus, and `viewport-fit=cover`
  plus `env(safe-area-inset-*)` keeps content clear of the notch and home
  indicator.
- Also handled: `prefers-reduced-motion`, landscape phones, and a print
  stylesheet for violation logs.

---

## Decisions where the spec was open

**`max_overflow` is 40, configurable.** The spec says 40 in one section and 10
in another. 40 is the default; set `DB_MAX_OVERFLOW=10` for the stricter value.
Postgres starts with `max_connections=300` to cover 4 workers × (20 + 40).

**No negative marking.** Every paper states this, so `negative_marks` is 0
throughout. The column and the scoring logic both exist — set it per-paper in
`seed_data/manifest.json`, or per-question on import.

**Filenames follow the spec's mapping exactly**, and `manifest.json` also lists
short aliases, so dropping in either `MATHS.csv` or `maths.csv` works.

**Candidates come from `Application_Details_23.xlsx`**, which carries names,
mobile numbers and programmes as well as the 50 emails. `seed_data/candidates.csv`
is generated from it; a plain `candidates.txt` of one email per line still
works and is used when the CSV is absent. Regenerate with
`python tools/build_candidates.py <workbook.xlsx> seed_data`.

---

## Architecture notes worth knowing

### The signalling gateway is Redis-backed, not in-memory

With `workers = 4`, a candidate's WebSocket and the proctor's WebSocket land on
the same process only about a quarter of the time. An in-memory socket registry
would drop roughly three quarters of proctoring feeds, intermittently — the
worst possible failure mode on exam day. Each worker keeps a local registry and
subscribes to one Redis pub/sub channel; sending is local-first with a publish
fallback. See `backend/app/services/signaling.py`.

### The server owns the clock

`phase1_hard_stop` / `phase2_hard_stop` are written once, at phase start, from
the server's clock. Every answer save, heartbeat and page load calls
`enforce_deadline()`, which auto-submits a phase whose deadline has passed. The
browser countdown is display only, re-synchronised from the server's
`seconds_left` on every response. Changing the system clock, reloading or
disconnecting gains nothing.

### Phase 1 cannot leak into Phase 2

Structurally, not by filtering: Phase 1 questions carry `phase = PHASE_1` and
`domain_id IS NULL`; Phase 2 allocation queries `phase = PHASE_2 AND domain_id =
:chosen`. A database `CHECK` constraint enforces that pairing, and
`start_phase2()` additionally asserts the two id sets are disjoint.

### Question and option order are frozen per candidate

Shuffled once at phase start with an RNG seeded from the session id, then
written into `exam_sessions.question_order` / `option_order`. A refresh returns
the same paper in the same order; different candidates get different orders;
the allocation is reproducible from the session id for audit.

### Text normalisation

Stored text uses **NFC**, not NFKC. NFKC is a compatibility fold that rewrites
`x²` to `x2` and `½` to `1/2`, which silently corrupts the mathematics paper.
The aggressive fold is applied only to the MD5 duplicate key, so two imports
differing by a superscript still collide as duplicates.

Because Postgres treats `NULL`s as distinct in a `UNIQUE` constraint, the
composite constraint cannot catch Phase 1 duplicates (whose `domain_id` is
always `NULL`); a partial unique index `uq_question_hash_phase1` closes that gap.

### Strict role separation

There is no shared `get_current_user`. Candidate and admin tokens carry
different `scope` claims, ride in differently named `HttpOnly` cookies, and are
validated by separate dependencies. A candidate token presented to an admin
endpoint fails at the scope check before any database lookup.
`tests/test_auth_separation.py` asserts this in both directions.

---

## The importer

### Three input layouts, tried in order

1. **Full schema** — carries `phase` and `domain_code` on every row. Download
   the template from **Admin → Question bank**.
2. **Compact schema** — what your corrected papers use:
   `question_number, question_text, option_a–d, correct_answer`. It says
   nothing about placement, so phase, domain and marks come from
   `manifest.json` when seeding, or from the import form in the admin UI.
3. **Raw question paper** — a Word/Excel export with numbered questions and an
   answer-key block. This was the shape of the original files; the tolerant
   parser is kept for anything that arrives that way again, and handles four
   different answer-key layouts.

### Guarantees

- **Two-pass and all-or-nothing.** Pass 1 validates every row against the real
  domain codes and writes nothing. If any row fails, the import is rejected and
  the UI lists **every** problem with its spreadsheet row number and column.
  Pass 2 runs only on a clean file, inside one SAVEPOINT.
- **A row that cannot be keyed is withheld and named**, not defaulted and not
  fatal to its 49 neighbours.
- **An ambiguous numeric `correct_answer` is rejected, not guessed.** On a paper
  with options `3/4/5/6`, the value `4` matches option B's text *and* option D's
  position, so the row is refused with an explanation.
- **UTF-8 BOM** is stripped (`utf-8-sig`), with UTF-16 / CP1252 / Latin-1
  fallbacks, so Tamil and mathematical symbols survive.
- **Format is sniffed from magic bytes**, not the extension — the English paper
  originally arrived as an XLSX named `.csv`.
- **Only the delimiter is sniffed, never the quoting.** `csv.Sniffer` reports
  `doublequote=False` on a file containing `""%d""`, which shreds the row across
  columns; CS Q46 (`printf("%d", x);`) is exactly that case. Quoting rules come
  from `csv.excel`. There is a regression test.

---

## Project layout

```
phd-exam-portal/
├── docker-compose.yml          backend · postgres · redis · coturn · nginx · certbot
├── Makefile                    make up / logs / seed / inspect / test
├── .env.example
├── nginx/  conf.d/{default.conf, ssl.conf.disabled, _proxy.inc}
├── coturn/turnserver.conf
└── backend/
    ├── Dockerfile · gunicorn_conf.py · requirements.txt · pytest.ini
    ├── alembic/versions/0001_initial_schema.py
    ├── seed_db.py                  idempotent seeding, three-tier ingest
    ├── seed_data/
    │   ├── manifest.json           paper → phase/domain mapping (+ aliases)
    │   ├── candidates.csv / .txt   generated from the registrar's workbook
    │   ├── Application_Details_23.xlsx
    │   └── <ten question papers, spec filenames>
    ├── tools/
    │   ├── inspect_papers.py       dry-run ingest quality report
    │   └── build_candidates.py     workbook → candidates.csv/.txt
    ├── tests/  (+ fixtures/ holding the original raw-format papers)
    └── app/
        ├── main.py · enums.py
        ├── core/     config · security · deps (RBAC) · redis
        ├── db/       async engine, session factory
        ├── models/   11 tables, constraints, indexes
        ├── schemas/
        ├── services/
        │   ├── exam_service.py         state machine, timer, allocation
        │   ├── scoring.py
        │   ├── question_importer.py    strict two-pass CSV/XLSX import
        │   ├── simple_schema.py        compact-layout adapter
        │   ├── raw_paper_parser.py     tolerant legacy-paper parser
        │   ├── signaling.py            Redis-backed WebRTC relay
        │   ├── text_utils.py
        │   └── audit.py
        ├── routers/  auth_candidate · auth_admin · exam · violations ·
        │             admin · signaling · pages
        ├── templates/
        └── static/   css · js · img (drop logo.png here)
```

---

## Operations

```bash
make up          # build, migrate, seed, serve
make logs        # tail the backend (the seed report prints here)
make inspect     # re-run the ingest quality report
make reseed      # re-import question banks only (idempotent)
make seed        # full re-seed (idempotent)
make test        # pytest
make psql        # psql shell
make clean       # stop and DELETE all data
```

### Enabling HTTPS

1. Point your domain at the host; set `CERTBOT_DOMAIN` / `CERTBOT_EMAIL` in `.env`.
2. `docker compose run --rm certbot`
3. `mv nginx/conf.d/ssl.conf.disabled nginx/conf.d/ssl.conf`, replacing
   `exam.example.edu` inside it.
4. Uncomment the redirect block in `nginx/conf.d/default.conf`.
5. Set `COOKIE_SECURE=true` in `.env`.
6. `docker compose restart nginx backend`

Nginx forwards `X-Forwarded-Proto $scheme`, so the backend marks cookies
`Secure` correctly once TLS is on.

### Before exam day

- [ ] Supply the **Tamil answer key** (the last outstanding data item).
- [ ] Add your **`logo.png`**.
- [ ] `SECRET_KEY` — `python -c "import secrets; print(secrets.token_urlsafe(64))"`.
- [ ] Change `Admin@123` and `POSTGRES_PASSWORD`.
- [ ] Enable HTTPS and set `COOKIE_SECURE=true`. WebRTC camera capture requires
      a secure context in every browser except on `localhost`.
- [ ] In `coturn/turnserver.conf`, set `external-ip` to the server's public
      address and change the TURN password. Without `external-ip`, relay
      candidates carry a private address and remote feeds never connect.
- [ ] Open UDP 3478 and 49160–49200 for TURN.
- [ ] Set the exam window (`exams.start_time` / `hard_stop_time`) — unset means
      "open now, closes when each candidate's own timer runs out".
- [ ] Dry-run with two browsers: one candidate, one proctor.

---

## Testing

```bash
make test                                              # everything
docker compose exec backend python -m pytest tests/test_simple_schema.py -q
```

| Suite | Tests | Needs |
|---|---:|---|
| `test_raw_paper_parser.py` | 23 | nothing |
| `test_question_importer.py` | 29 | nothing |
| `test_simple_schema.py` | 24 | nothing |
| `test_exam_flow.py` | 17 | Postgres |
| `test_auth_separation.py` | 15 | Postgres + Redis |

The **76 database-free tests were run and pass.** They cover the compact-schema
adapter, the CSV-quoting regression, withholding rather than guessing, Tamil and
mathematical-symbol fidelity, and the expected import count for every supplied
paper — so a future re-export that silently degrades is caught.

The integration suites are written but **have not been executed here**: this
build environment has no Docker daemon and its egress policy blocks PyPI, so
FastAPI, SQLAlchemy and pytest could not be installed. Run `make test` once the
stack is up; that is the first thing to do.

The interface was verified by rendering every template and screenshotting it at
six widths under Chromium. Four layout bugs were found and fixed that way —
a hidden `<video>` still occupying its box (author `display` beats the `hidden`
attribute), a stale loading placeholder shifting the proctoring grid, an inline
`grid-template-columns` overriding the mobile media query, and a brand title
that could not ellipsise because its box was inline.

---

## API reference

### Candidate
| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/auth/candidate/login` | email + password → `HttpOnly` cookie |
| `POST` | `/api/auth/candidate/logout` | |
| `GET` | `/api/exam/state` | status, server time, seconds left |
| `POST` | `/api/exam/phase1/start` · `/submit` | allocate + start clock; score + lock |
| `GET` | `/api/exam/domains` | domains that actually have a paper |
| `POST` | `/api/exam/domain` | lock the choice, one way |
| `POST` | `/api/exam/phase2/start` · `/submit` | |
| `GET` | `/api/exam/paper` | current paper; never exposes `is_correct` |
| `POST` | `/api/sessions/{id}/answer` | debounced autosave, server-side upsert |
| `POST` | `/api/sessions/{id}/heartbeat` | presence + authoritative clock resync |
| `POST` | `/api/violations` | report a violation with a snapshot |

### Admin
| Method | Path | Minimum role |
|---|---|---|
| `POST` | `/api/auth/admin/login` | — |
| `GET` | `/api/admin/dashboard` | proctor |
| `GET` | `/api/admin/sessions/live` | proctor |
| `GET` | `/api/admin/sessions/{id}/violations` | proctor |
| `GET` | `/api/admin/candidates` | admin |
| `PATCH` | `/api/admin/candidates/{id}/status` | admin |
| `POST` | `/api/admin/questions/import` | admin |
| `GET` | `/api/admin/questions/template` | admin |
| `GET` | `/api/admin/domains` | proctor |
| `GET` | `/api/admin/results/export` | admin |
| `POST` | `/api/admin/results/publish` | super admin |
| `POST` | `/api/admin/exam/{id}/toggle` | super admin |

### WebSocket
| Path | Who |
|---|---|
| `/ws/signaling/{candidate_id}` | candidate; a mismatched id is closed with 4403 |
| `/ws/proctor` | admin |

---

## Known limitations

- **`MULTIPLE_FACES` and `NO_FACE` are defined but never raised.** They need a
  face-detection model in the browser; the enum, API and proctor UI are ready,
  so adding a detector is a client-side change only. Everything else — tab
  switch, focus loss, fullscreen exit, camera loss, copy/paste, devtools,
  network loss — is live.
- **Descriptive (`TEXT`) answers are recorded but not auto-scored.** Stored and
  counted as attempted with `awarded_marks = 0`, awaiting manual grading. No
  supplied paper uses them.
- **Violation snapshots live on a Docker volume**, not object storage. At 50
  candidates that is a few hundred MB; for a much larger cohort point
  `SNAPSHOT_DIR` at S3 or equivalent.
- **The admin grid subscribes to every visible tile at once.** Comfortable well
  past 50 concurrent candidates; beyond a few hundred, page the grid or add an
  SFU rather than relying on mesh WebRTC.
