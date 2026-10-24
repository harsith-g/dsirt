#!/usr/bin/env python3
"""Dry-run the seed ingest and print a per-paper quality report.

Touches no database, so it can be run against a new batch of papers before
seeding to see exactly what would be imported and what would be withheld:

    python tools/inspect_papers.py [seed_data_dir] [--verbose]
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.services.question_importer import parse_rows, read_tabular  # noqa: E402
from app.services.raw_paper_parser import parse_paper, to_import_rows  # noqa: E402
from app.services.simple_schema import adapt, looks_like_simple_schema  # noqa: E402

GREEN, YELLOW, RED, DIM, RESET = "\033[32m", "\033[33m", "\033[31m", "\033[2m", "\033[0m"


def prepare(data: bytes, entry: dict, label: str):
    """Mirror of seed_db._prepare_rows, kept dependency-free."""
    phase = entry["phase"]
    domain_code = entry.get("domain_code")
    try:
        tabular = read_tabular(data)
    except Exception:
        tabular = []

    if tabular and "phase" in tabular[0]:
        return tabular, [], "strict", len(tabular)
    if looks_like_simple_schema(tabular):
        rows, report = adapt(
            tabular,
            phase=phase,
            domain_code=domain_code,
            marks=float(entry.get("marks", 1.0)),
            negative_marks=float(entry.get("negative_marks", 0.0)),
            source_prefix=entry.get("source_prefix", ""),
        )
        return rows, report.notes(), "compact", report.total

    questions, report = parse_paper(data, label)
    rows = to_import_rows(
        questions,
        phase=phase,
        domain_code=domain_code,
        marks=float(entry.get("marks", 1.0)),
        negative_marks=float(entry.get("negative_marks", 0.0)),
        source_prefix=entry.get("source_prefix", ""),
    )
    notes = []
    if report.rejected_no_key:
        notes.append(f"no answer-key entry: Q{report.rejected_no_key}")
    if report.rejected_no_options:
        notes.append(f"options missing: Q{report.rejected_no_options}")
    if report.rejected_few_options:
        notes.append(f"fewer than 2 options: Q{report.rejected_few_options}")
    if report.rejected_bad_key:
        notes.append(f"key points at a missing option: Q{report.rejected_bad_key}")
    if report.missing_question_text:
        notes.append(f"stem missing near Q{report.missing_question_text}")
    return rows, notes, "raw paper", report.parsed


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    verbose = "--verbose" in sys.argv or "-v" in sys.argv
    root = pathlib.Path(args[0]) if args else pathlib.Path("seed_data")

    manifest_path = root / "manifest.json"
    if not manifest_path.exists():
        print(f"No manifest.json in {root}", file=sys.stderr)
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    known_codes = {d["code"].upper() for d in manifest["domains"]}

    total_in = total_ok = 0
    problems: list[tuple[str, str]] = []

    print(f"{'paper':<50}{'phase':<9}{'dom':<6}{'rows':>5}{'ok':>5}{'held':>6}  layout / notes")
    print("-" * 120)

    for entry in manifest["papers"]:
        label = entry["file"]
        path = None
        for name in [label, *entry.get("aliases", [])]:
            if (root / name).exists():
                path = root / name
                break
        short = label if len(label) <= 48 else label[:45] + "..."
        if path is None:
            print(f"{RED}{short:<50}{'':<9}{'':<6}{'MISSING':>16}{RESET}")
            problems.append((label, "file not found"))
            continue

        rows, notes, layout, seen = prepare(path.read_bytes(), entry, label)
        if rows:
            parsed, report = parse_rows(rows, known_codes)
            usable = len(parsed) if report.ok else 0
        else:
            # Everything was withheld upstream; that is not a validation error.
            parsed, report, usable = [], None, 0
        held = seen - usable
        total_in += seen
        total_ok += usable

        colour = GREEN if held == 0 else (RED if usable == 0 else YELLOW)
        print(
            f"{colour}{short:<50}{RESET}{entry['phase'].replace('PHASE_','P'):<9}"
            f"{(entry.get('domain_code') or '-'):<6}{seen:>5}{usable:>5}{held:>6}  "
            f"{DIM}{layout}{RESET}"
        )
        for note in notes:
            print(f"{'':<50}{YELLOW}  ! {note}{RESET}")
        if report is not None and not report.ok:
            for err in report.errors[:5]:
                print(f"{'':<50}{RED}  x {err}{RESET}")
            problems.append((label, "validation failed"))
        elif usable == 0:
            problems.append((label, notes[0] if notes else "nothing usable"))
        if verbose:
            for row in parsed[:2]:
                print(f"{'':<52}{DIM}{row.question_text[:66]}{RESET}")

    print("-" * 120)
    print(f"{'TOTAL':<50}{'':<9}{'':<6}{total_in:>5}{total_ok:>5}{total_in - total_ok:>6}")
    if problems:
        print()
        print(f"{YELLOW}Papers needing attention:{RESET}")
        for label, reason in problems:
            print(f"  - {label}: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
