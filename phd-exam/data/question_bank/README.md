# Question Bank — Ready-to-Import Fixture

This folder contains a normalized, ready-to-upload question bank built from the
subject CSVs supplied for this refactor, verified against the real
`backend/services/ingestion.py` parser (see PR description for how).

## What's here

- One canonical CSV per domain (`agricultural_agronomy.csv`, `computer_science.csv`, …).
- `question_bank.zip` — the same 9 CSVs bundled together for a single upload via
  **Admin Dashboard → Question Bank → Upload → question_bank.zip**
  (or `POST /admin/questions/upload`).

## Schema

Every row is fully self-describing — no reliance on filename-based domain
inference — so each CSV also works standalone via the single-file importer
(`POST /questions/bulk-import`):

| Column          | Meaning                                              |
|-----------------|-------------------------------------------------------|
| `domain`        | Canonical domain name (must match `backend/config.py`'s `DOMAINS` list) |
| `phase`         | `1` (Research Methodology) or `2` (everything else)  |
| `question_text` | The question                                          |
| `option_a`..`option_d` | The four MCQ options                          |
| `correct_option`| 0-based index (0=A, 1=B, 2=C, 3=D) — unambiguous      |

## Domain coverage

| Domain                       | Questions | Notes |
|-------------------------------|:---------:|-------|
| Research Methodology          | 50 | Phase 1 |
| Agricultural - Agronomy       | 50 | |
| Agricultural - Entomology     | 50 | |
| Agricultural - Pathology      | 50 | |
| Bio-Science                   | 50 | |
| Computer Science              | **48** | Source file's question_number 38 and 39 have a **blank correct-answer** — genuinely missing data, not a parsing bug. Those 2 rows are excluded; the other 48 import cleanly. Add 2 more questions to this domain before relying on it for a live exam (`EXAM_PAPER2_COUNT = 50` in `backend/config.py`). |
| English                       | 50 | |
| Mathematics                   | 50 | |
| Tamil                          | 50 | |
| **Total**                      | **448** | |

## Deliberately excluded

**Commerce & Management Studies** is not included. The supplied
`commerce_management.csv` (and the Commerce rows in `all_questions_combined.csv`)
have every `correct_answer` cell blank — there is no answer key to import. Per
the user's decision, this domain is left out of the fixture entirely rather than
importing 50 unscoreable questions. Supply a corrected CSV with the
`correct_answer`/`correct_option` column filled in and it can be added the same
way (a 10th canonical CSV, folded into `question_bank.zip`).

## Regenerating this fixture

Built by re-running the real ingestion parser (`backend.services.ingestion.parse_csv_rows`)
against each source CSV and writing out the normalized rows above — not a
hand-copy. If the source subject CSVs change, re-run the same process to
refresh these files so they stay ground-truth-accurate to what the parser
actually accepts.
