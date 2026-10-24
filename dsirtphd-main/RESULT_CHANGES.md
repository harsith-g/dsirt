# Result pages, roll numbers and hall controls

What changed, why, and the two things that need a decision from you.

---

## Deploying

This is a Node app under PM2 — **not** Docker, despite the unused `backend/`
Python directory left over from an earlier attempt. On the server:

```sh
cd /path/to/dsirtphd
git pull                 # or unzip over the top
npm install              # only if package.json changed — it did not
npm run build            # esbuild -> dist/server.cjs
pm2 restart phd-exam
pm2 logs phd-exam --lines 40
```

`npm run build` is **required**. PM2 runs `dist/server.cjs`, not `server.ts`, so
editing the TypeScript alone changes nothing until you rebuild.

The three PNGs under `frontend/static/img/` are served straight from disk, so
they are live as soon as the files are there.

Back up `data/runtime_state.json` first. It holds the live exam.

---

## 1. Result pages

### Removed

* `Official Merit Gazette & Consolidated Result Publication` — the `<title>`,
  the grey bar above the table, and the heading under the institution name.
* `Official Merit Gazette and Consolidated Results Register` — the caption
  next to the Print button.
* The three typed-out header lines on every scorecard — institution name,
  `Directorate of Research • Ph.D Entrance Examination`, and
  `Official Candidate Scorecard & Evaluation Record`. The letterhead image
  replaces all three.
* The five-signature block on the scorecards.

### Changed

* `Exam Session: … (RET) 2025-2026` → `2026 - 2027`, in one place:
  `EXAM_SESSION_LABEL` in `server.ts`. Change it there next year.
* Every `Phase 1` / `Phase 2` a person reads is now `Paper 1` / `Paper 2` —
  table headers, score rows, CSV and Excel exports, the candidate's screens,
  the API messages. The `PHASE_1` / `PHASE_2` values in the database, the
  `phase1_*` / `phase2_*` fields and `current_phase` are **untouched**; only
  display text changed.

### Added

* The DSIRT letterhead at the top of both result pages and on the login page.
* The emblem as a light watermark behind the text, on every page of both.
* A verification panel where the signatures were.

### Why the watermark is an `<img>` and not a CSS background

These PDFs are printed from HTML by the browser — there is no server-side PDF
library. A CSS `background-image` is only painted when **Background graphics**
is ticked in the print dialog, and it is off by default in Chrome. The
watermark would have looked right on screen and been missing from the PDF, and
nobody would have noticed until the results were handed out. An `<img>` is page
content and always prints. The 8% fade is baked into the PNG's alpha channel
for the same reason — there is no CSS `opacity` for the print path to drop.

`position: fixed` makes Chromium repeat it on every printed page. Verified on a
43-page gazette: the emblem is on page 1 and on page 43.

### Where the Exam Session line sits

Directly under the letterhead, because on an official document the
institutional letterhead is the topmost element. To put it above instead, move
the `${sessionLine}` line above the `<img>` inside `letterhead()` in
`server.ts` — one line, nothing else depends on the order.

### The gazette's own signature row

Still there, on the summary page only. You asked for the signatures off the
**mark sheet**, and every scorecard — the standalone one and the ones appended
to the bulk PDF — now ends with the verification panel instead. The gazette
summary is a different document and a Controller genuinely signs one. Say the
word and it goes too.

---

## 2. Roll numbers — one decision for you

New roll numbers are now `PHD2026-###`. The prefix lives in one place:

```ts
export const ROLL_PREFIX = process.env.ROLL_PREFIX || 'PHD2026';
```

**Candidates already in `data/runtime_state.json` keep `PHD2025-###`.** That is
deliberate, not an oversight:

* the 43-page gazette you already published lists `PHD2025-000` onwards.
  Renumbering makes every printed copy disagree with the portal.
* anything that stored the number as text elsewhere — exports, emails already
  sent — keeps the old value.

If those candidates are pre-go-live test data that nobody has used, renumber
them with the server stopped:

```sh
pm2 stop phd-exam
cp data/runtime_state.json ~/runtime_state.backup.json
python3 - <<'PY'
import json, re
p = 'data/runtime_state.json'
d = json.load(open(p))
n = 0
for u in d.get('users', []):
    if u.get('roll_no', '').startswith('PHD2025-'):
        u['roll_no'] = u['roll_no'].replace('PHD2025-', 'PHD2026-'); n += 1
json.dump(d, open(p, 'w'), indent=2)
print(f'renumbered {n}')
PY
pm2 start phd-exam
```

Check the printed count is what you expect before restarting. If it is wrong,
copy the backup back.

---

## 3. Hall controls

Three buttons in the admin header. All three are admin-only and act on every
candidate at once, so each reports what it skipped rather than forcing it.

### End Paper 1 — Break for All → `POST /admin/exam/break-all`

Submits Paper 1 for everyone still writing it and sends them into the break.

Leaves alone: anyone already on the break, anyone already in Paper 2 (moving
them back would discard their answers), anyone finished, anyone not started —
and says how many of each.

**Paper 2's clock is not affected.** It starts when the candidate opens Paper 2,
not when the break starts, so a long break does not eat into their time.

### Open Paper 2 for All → `POST /admin/exam/open-paper2-all`

Opens Paper 2 for everyone on the break without waiting out the timer. This is
the bulk version of the **Open P2 now** button already on the Live Monitor.

### Reset → `POST /admin/exam/reset`

Clears every exam attempt so the hall can be used again.

* **Kept:** candidate and admin accounts, passwords, the question bank.
* **Cleared:** every session, answer, violation and result.

Before clearing anything it writes all of it — including the computed results,
which cannot be recovered from the raw data afterwards — to
`data/archive/exam-<timestamp>.json`. If that write fails, **nothing is
cleared** and you get an error instead.

It also refuses while anyone is still writing or on the break, and requires the
word `RESET` to be typed. There is no undo, so it asks for more than a click.

`data/archive/` is in `.gitignore` — those files hold candidate results. Back
them up, do not commit them.

---

## Verified

* `npx tsc --noEmit` produces no new errors against the original.
* The scorecard and gazette were generated by running the **real route
  handlers** against the real candidate list (51 candidates), then printed to
  PDF through Chromium with background graphics off — the default, and the case
  that silently kills a background-image watermark.
* 25 tests over the three new endpoints: correct counts, correct skips, no
  effect on candidates in the wrong state, both guards on reset, the archive
  written and readable, accounts and questions surviving, and every button
  safe to press twice.

Not verified here: `npm install` and `npm run build` could not run — this
machine's network blocks the npm registry. Both are unchanged from what already
works on your server, and no dependency was added.
