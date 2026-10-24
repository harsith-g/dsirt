"""
One-time, NON-DESTRUCTIVE repair for MCQ questions whose options got
merged into a single string during the original import (root cause
fixed in backend/services/ingestion.py).

WHAT THIS DOES:
  - Re-parses each domain's source .docx (using the FIXED parser).
  - For each existing Question row that looks corrupted (options length
    != 4, or an option string still contains an embedded "B."/"C."/"D."
    marker), overwrites ONLY that row's `options` and `correct_option`.
  - Matches existing DB rows to freshly-parsed rows by position
    (domain + phase, ordered by order_no) — it does NOT delete or
    recreate any row, so Question.id stays stable and every
    ExamSession / Answer / CandidateResult foreign key stays valid.

WHAT THIS NEVER DOES:
  - Never touches ExamSession, Answer, Violation, CandidateResult.
  - Never deletes a Question row.
  - Never writes anything unless you pass --apply.

USAGE (run from the project root, same place you'd run
force_import_questions.py):

    # 1. Dry run first — just prints a report, changes nothing:
    python3 repair_mcq_options.py

    # 2. If the report looks right, actually apply the fix:
    python3 repair_mcq_options.py --apply

If a domain's row count doesn't match what the fixed parser produces
from the .docx, that domain is SKIPPED and flagged for manual review
instead of guessing an alignment — safer to leave those alone than to
silently mis-map options to the wrong question.
"""
import os
import re
import sys

sys.path.append('.')
from backend.database import SessionLocal
from backend.models import Question
from backend.services.ingestion import parse_docx_questions

DOMAIN_MAPPING = {
    "Research Methodology": "DSU_Research_Methodology_Set_II.docx",
    "Mathematics": "MATHS.docx",
    "Tamil": "Ph.D - ENTRANCE EXAM  TAMILTAMILQuestion.doc",
    "Agricultural - Agronomy": "AGRONOMY -Final.docx",
    "Bio-Science": "LIF SCIENCE.docx",
    "Agricultural - Entomology": "entomology.docx",
    "Agricultural - Pathology": "Pathology.docx",
    "English": "PhD- Entrance  Eng QP.docx",
    "Commerce & Management Studies": "PhD_Entrance_Exam_Commerce_Management_Alternate_Questions.docx",
    "Computer Science": "COMPUTER SCIENCE ENTRANCE TEST QUESTIONS.docx",
}

_EMBEDDED_MARKER_RE = re.compile(r'[\sA-Za-z]\s[B-D][\.\)]\s')


def is_corrupted(options):
    if not options:
        return True
    if len(options) != 4:
        return True
    return any(_EMBEDDED_MARKER_RE.search(o) for o in options)


def run_repair(dry_run=True):
    db = SessionLocal()
    fixed, already_ok, flagged = 0, 0, []

    for domain, filename in DOMAIN_MAPPING.items():
        filepath = os.path.join('files_questiond', filename)
        if not os.path.exists(filepath):
            flagged.append(f"{domain}: source file '{filename}' not found — skipped")
            continue

        with open(filepath, 'rb') as f:
            file_bytes = f.read()
        phase = 1 if domain == "Research Methodology" else 2
        freshly_parsed = parse_docx_questions(file_bytes, domain, phase)

        existing = (
            db.query(Question)
            .filter(Question.domain == domain, Question.phase == phase)
            .order_by(Question.order_no)
            .all()
        )

        if len(existing) != len(freshly_parsed):
            flagged.append(
                f"{domain}: DB has {len(existing)} rows, re-parse produced "
                f"{len(freshly_parsed)} — SKIPPED (counts must match to align safely, needs manual look)"
            )
            continue

        for db_q, new_q in zip(existing, freshly_parsed):
            if is_corrupted(db_q.options):
                print(f"  [{domain}] order_no={db_q.order_no}: "
                      f"{db_q.options} -> {new_q.options}")
                if not dry_run:
                    db_q.options = new_q.options
                    db_q.correct_option = new_q.correct_option
                fixed += 1
            else:
                already_ok += 1

    if not dry_run:
        db.commit()
    db.close()

    print()
    print(f"{'APPLIED' if not dry_run else 'DRY RUN (nothing written)'}")
    print(f"Fixed: {fixed}   Already OK: {already_ok}")
    if flagged:
        print("\nNEEDS MANUAL REVIEW:")
        for f in flagged:
            print(" -", f)


if __name__ == '__main__':
    run_repair(dry_run=('--apply' not in sys.argv))
