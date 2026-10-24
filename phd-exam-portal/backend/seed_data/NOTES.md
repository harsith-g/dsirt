# Question-bank provenance

Audit notes for anything in `seed_data/` that does not come verbatim from the
originally supplied papers. Everything not listed here is unmodified.

## Manual answer-key entries

| File | Question | Key set | Authority | Date |
|---|---|---|---|---|
| `COMPUTER-SCIENCE-ENTRANCE-TEST-QUESTIONS-_1_.csv` | Q38 | `B` — *Encoder* | Instructed by the examination cell, 21 Sep 2026 | 2026-09-21 |
| `COMPUTER-SCIENCE-ENTRANCE-TEST-QUESTIONS-_1_.csv` | Q39 | `B` — *Output of a combinational circuit depends only on current inputs* | Instructed by the examination cell, 21 Sep 2026 | 2026-09-21 |

Both questions arrived with an empty `correct_answer` column: the answer-key
grid in the original paper skipped them (its rows 8–9 repeat 29/30/40 in the
positions where 38/39 belong). The keys above were confirmed by the
examination cell before being entered.

No other answer key in this directory has been inferred, defaulted or
reconstructed. The importer withholds any question it cannot key from the
source and reports it by number; see `make inspect`.

## Outstanding

| File | Gap | Needed |
|---|---|---|
| `Ph.D-ENTRANCE-EXAM-TAMILTAMILQuestion-_1_.csv` | All 50 `correct_answer` cells are empty. The Tamil text itself is correct UTF-8 and renders properly. | Fill the `correct_answer` column with A/B/C/D and re-run `make reseed`, or upload through **Admin → Question bank** (phase *Phase 2*, domain *TAM*). |

Until then the Tamil domain is seeded but `is_active = false`, so candidates
cannot select a domain with no paper behind it. It becomes selectable
automatically once its questions import.

## File naming

Papers are stored under the exact filenames given in the specification.
`manifest.json` also lists short aliases (`maths.csv`, `tamil.csv`, …), so a
replacement dropped in under either name is picked up.
