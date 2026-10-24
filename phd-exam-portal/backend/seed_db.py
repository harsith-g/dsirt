#!/usr/bin/env python3
"""Idempotent database seeding.

Creates, in order:

1. the default admin (``admin@examportal.edu`` / ``Admin@123``, bcrypt-hashed),
2. the Phase 2 domains listed in ``seed_data/manifest.json``,
3. the examination row,
4. the 50 candidates -- from ``seed_data/candidates.csv`` when present
   (email + registration number + name + programme), otherwise from
   ``seed_data/candidates.txt`` (one email per line), status APPROVED and
   password ``dsirt@123``, bcrypt-hashed,
5. the question banks, by running each supplied paper through the legacy
   parser and then the strict two-pass importer.

Re-running is safe: existing rows are matched on their natural keys and the
MD5 duplicate check keeps the question bank from doubling.

    python seed_db.py                 # seed everything
    python seed_db.py --questions-only
    python seed_db.py --reset-passwords
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import pathlib
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from sqlalchemy import func, select  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db.session import session_scope  # noqa: E402
from app.enums import AdminRole, CandidateStatus, Phase  # noqa: E402
from app.models import Admin, Candidate, Domain, Exam, Question  # noqa: E402
from app.services.question_importer import (  # noqa: E402
    parse_rows,
    persist,
    read_tabular,
)
from app.services.raw_paper_parser import parse_paper, to_import_rows  # noqa: E402
from app.services.simple_schema import adapt, looks_like_simple_schema  # noqa: E402

SEED_DIR = pathlib.Path(__file__).resolve().parent / settings.SEED_DATA_DIR

GREEN, YELLOW, RED, DIM, RESET = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"


def ok(msg: str) -> None:
    print(f"{GREEN}  OK  {RESET}{msg}")


def warn(msg: str) -> None:
    print(f"{YELLOW} WARN {RESET}{msg}")


def fail(msg: str) -> None:
    print(f"{RED} FAIL {RESET}{msg}")


def head(msg: str) -> None:
    print(f"\n{msg}\n{'-' * len(msg)}")


def load_manifest() -> dict:
    path = SEED_DIR / "manifest.json"
    if not path.exists():
        raise SystemExit(f"Missing {path}. It maps each paper onto a phase and domain.")
    return json.loads(path.read_text(encoding="utf-8"))


# ------------------------------------------------------------------- admin
async def seed_admin(db) -> None:
    head("Admin")
    email = settings.SEED_ADMIN_EMAIL.lower()
    admin = (await db.execute(select(Admin).where(Admin.email == email))).scalar_one_or_none()
    if admin:
        ok(f"admin already present: {email}")
        return
    db.add(
        Admin(
            email=email,
            hashed_password=hash_password(settings.SEED_ADMIN_PASSWORD),
            full_name="System Administrator",
            role=AdminRole.SUPER_ADMIN,
            is_active=True,
        )
    )
    await db.flush()
    ok(f"created super admin {email}")
    warn("change this password before the exam goes live")


# ----------------------------------------------------------------- domains
async def seed_domains(db, manifest: dict) -> dict[str, Domain]:
    head("Domains")
    out: dict[str, Domain] = {}
    for entry in manifest["domains"]:
        code = entry["code"].upper()
        domain = (
            await db.execute(select(Domain).where(Domain.code == code))
        ).scalar_one_or_none()
        if domain is None:
            domain = Domain(
                code=code,
                name=entry["name"],
                description=entry.get("description"),
                is_active=True,
            )
            db.add(domain)
            await db.flush()
            ok(f"created domain {code} - {entry['name']}")
        else:
            ok(f"domain exists {code}")
        out[code] = domain
    return out


# -------------------------------------------------------------------- exam
async def seed_exam(db, manifest: dict) -> Exam:
    head("Examination")
    cfg = manifest["exam"]
    exam = (
        await db.execute(select(Exam).where(Exam.title == cfg["title"]))
    ).scalar_one_or_none()
    if exam is None:
        exam = Exam(
            title=cfg["title"],
            description=cfg.get("description"),
            phase1_duration_mins=cfg.get("phase1_duration_mins", 60),
            phase2_duration_mins=cfg.get("phase2_duration_mins", 60),
            phase1_question_count=cfg.get("phase1_question_count", 50),
            phase2_question_count=cfg.get("phase2_question_count", 50),
            is_active=True,
        )
        db.add(exam)
        await db.flush()
        ok(f"created exam '{exam.title}'")
    else:
        ok(f"exam exists '{exam.title}'")
    return exam


# -------------------------------------------------------------- candidates
def _read_candidate_records() -> list[dict[str, str]]:
    csv_path = SEED_DIR / "candidates.csv"
    txt_path = SEED_DIR / "candidates.txt"

    if csv_path.exists():
        with csv_path.open(encoding="utf-8-sig", newline="") as fh:
            rows = [r for r in csv.DictReader(fh) if (r.get("email") or "").strip()]
        if rows:
            return rows

    if txt_path.exists():
        emails = [
            line.strip().lower()
            for line in txt_path.read_text(encoding="utf-8-sig").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        return [
            {
                "email": email,
                "registration_number": f"DSIRT2026{i:04d}",
                "full_name": email.split("@")[0].replace(".", " ").title(),
                "phone": "",
                "programme": "",
            }
            for i, email in enumerate(dict.fromkeys(emails), start=1)
        ]
    return []


async def seed_candidates(db, *, reset_passwords: bool) -> None:
    head("Candidates")
    records = _read_candidate_records()
    if not records:
        warn(
            f"no candidates.csv or candidates.txt in {SEED_DIR} -- skipping candidate seeding"
        )
        return

    hashed = hash_password(settings.SEED_CANDIDATE_PASSWORD)
    created = updated = skipped = 0

    for rec in records:
        email = (rec.get("email") or "").strip().lower()
        if not email:
            continue
        candidate = (
            await db.execute(select(Candidate).where(Candidate.email == email))
        ).scalar_one_or_none()
        if candidate is None:
            db.add(
                Candidate(
                    email=email,
                    registration_number=(rec.get("registration_number") or "").strip()
                    or f"DSIRT2026{created + 1:04d}",
                    hashed_password=hashed,
                    full_name=(rec.get("full_name") or "").strip() or email.split("@")[0],
                    phone=(rec.get("phone") or "").strip() or None,
                    programme=(rec.get("programme") or "").strip() or None,
                    status=CandidateStatus.APPROVED,
                )
            )
            created += 1
        elif reset_passwords:
            candidate.hashed_password = hashed
            updated += 1
        else:
            skipped += 1
    await db.flush()

    ok(f"created {created}, password-reset {updated}, already present {skipped}")
    if created:
        ok(f"all candidates seeded APPROVED with password '{settings.SEED_CANDIDATE_PASSWORD}'")


# ---------------------------------------------------------- question banks
def _resolve_paper(entry: dict) -> pathlib.Path | None:
    """Find the paper by its manifest name, then by any alias."""
    for name in [entry["file"], *entry.get("aliases", [])]:
        candidate = SEED_DIR / name
        if candidate.exists():
            return candidate
    return None


def _prepare_rows(data: bytes, entry: dict, label: str):
    """Turn a paper file into strict-importer rows.

    Three layouts are accepted, tried in order:

    1. the **strict schema** (carries `phase` and `domain_code` per row);
    2. the **compact schema** the corrected papers use;
    3. a **raw question paper** exported from Word or Excel, which the
       tolerant parser reads.

    Returns ``(rows, notes, source_description)``.
    """
    phase = entry["phase"]
    domain_code = entry.get("domain_code")
    marks = float(entry.get("marks", 1.0))
    negative = float(entry.get("negative_marks", 0.0))
    prefix = entry.get("source_prefix", "")

    try:
        tabular = read_tabular(data)
    except Exception:
        tabular = []

    if tabular and "phase" in tabular[0]:
        return tabular, [], "strict schema"

    if looks_like_simple_schema(tabular):
        rows, report = adapt(
            tabular,
            phase=phase,
            domain_code=domain_code,
            marks=marks,
            negative_marks=negative,
            source_prefix=prefix,
        )
        return rows, report.notes(), f"compact schema ({report.total} rows)"

    questions, report = parse_paper(data, label)
    rows = to_import_rows(
        questions,
        phase=phase,
        domain_code=domain_code,
        marks=marks,
        negative_marks=negative,
        source_prefix=prefix,
    )
    return rows, _withheld_notes(report), f"raw question paper ({report.parsed} parsed)"


async def seed_questions(db, manifest: dict, domains: dict[str, Domain]) -> None:
    head("Question banks")
    domain_ids = {code: d.id for code, d in domains.items()}
    known_codes = set(domain_ids)

    problem_papers: list[tuple[str, str]] = []

    for entry in manifest["papers"]:
        label = entry["file"]
        path = _resolve_paper(entry)
        if path is None:
            fail(f"{label}: file not found")
            problem_papers.append((label, "file not found"))
            continue

        short = label if len(label) <= 44 else label[:41] + "..."
        rows, notes, origin = _prepare_rows(path.read_bytes(), entry, label)

        if not rows:
            reason = notes[0] if notes else "nothing usable in the file"
            fail(f"{short}: 0 imported -- {reason}")
            for note in notes:
                print(f"        {DIM}{note}{RESET}")
            problem_papers.append((label, reason))
            continue

        parsed, import_report = parse_rows(rows, known_codes)
        if not import_report.ok:
            fail(f"{short}: validation failed")
            for err in import_report.errors[:10]:
                print(f"        {DIM}{err}{RESET}")
            problem_papers.append((label, "validation failed"))
            continue

        await persist(db, parsed, import_report, domain_ids)

        detail = [origin]
        if import_report.duplicates_in_db:
            detail.append(f"{import_report.duplicates_in_db} already present")
        ok(f"{short:<44} imported {import_report.imported:>3}  {DIM}({', '.join(detail)}){RESET}")
        for note in notes:
            warn(f"        {note}")

    print()
    p1 = (
        await db.execute(
            select(func.count(Question.id)).where(Question.phase == Phase.PHASE_1)
        )
    ).scalar_one()
    p2 = (
        await db.execute(
            select(func.count(Question.id)).where(Question.phase == Phase.PHASE_2)
        )
    ).scalar_one()
    ok(f"question bank now holds {p1} Phase 1 and {p2} Phase 2 questions")

    if problem_papers:
        print()
        warn("These papers produced no questions and need attention:")
        for label, reason in problem_papers:
            print(f"        - {label}: {reason}")


def _withheld_notes(report) -> list[str]:
    notes = []
    if report.rejected_no_key:
        notes.append(f"no answer-key entry: Q{report.rejected_no_key}")
    if report.rejected_no_options:
        notes.append(f"options missing in source: Q{report.rejected_no_options}")
    if report.rejected_few_options:
        notes.append(f"fewer than 2 usable options: Q{report.rejected_few_options}")
    if report.rejected_bad_key:
        notes.append(f"key points at a missing option: Q{report.rejected_bad_key}")
    if report.missing_question_text:
        notes.append(f"question stem missing in source near Q{report.missing_question_text}")
    if report.key_conflicts:
        notes.append(f"contradictory key entries: {report.key_conflicts}")
    return notes


# -------------------------------------------------------- domain activation
async def deactivate_empty_domains(db) -> None:
    """A domain with no questions must not be offered at Phase 2 selection."""
    head("Domain availability")
    rows = (
        await db.execute(
            select(Domain, func.count(Question.id))
            .join(
                Question,
                (Question.domain_id == Domain.id) & (Question.is_active.is_(True)),
                isouter=True,
            )
            .group_by(Domain.id)
            .order_by(Domain.name)
        )
    ).all()
    for domain, count in rows:
        count = int(count)
        should_be_active = count > 0
        if domain.is_active != should_be_active:
            domain.is_active = should_be_active
        if should_be_active:
            ok(f"{domain.code:<5} {domain.name:<24} {count:>3} questions  selectable")
        else:
            warn(
                f"{domain.code:<5} {domain.name:<24} {count:>3} questions  "
                f"HIDDEN from candidates until questions are imported"
            )
    await db.flush()


# -------------------------------------------------------------------- main
async def run(args: argparse.Namespace) -> int:
    manifest = load_manifest()
    print(f"Seeding from {SEED_DIR}")
    async with session_scope() as db:
        if not args.questions_only:
            await seed_admin(db)
        domains = await seed_domains(db, manifest)
        if not args.questions_only:
            await seed_exam(db, manifest)
            await seed_candidates(db, reset_passwords=args.reset_passwords)
        await seed_questions(db, manifest, domains)
        await deactivate_empty_domains(db)

    head("Done")
    print(f"  Candidate login : {settings.BASE_URL}/login")
    print(f"  Admin login     : {settings.BASE_URL}/admin/login")
    print(f"  Admin account   : {settings.SEED_ADMIN_EMAIL} / {settings.SEED_ADMIN_PASSWORD}")
    print(f"  Candidate pass  : {settings.SEED_CANDIDATE_PASSWORD}")
    print(f"  Completed at    : {datetime.now(timezone.utc).isoformat()}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--questions-only", action="store_true", help="import question banks only"
    )
    parser.add_argument(
        "--reset-passwords",
        action="store_true",
        help="reset every seeded candidate back to the default password",
    )
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
