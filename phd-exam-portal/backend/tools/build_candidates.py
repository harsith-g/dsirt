#!/usr/bin/env python3
"""Turn the registrar's Application Details workbook into seed inputs.

Produces two files in ``seed_data/``:

``candidates.txt``
    One email per line -- the plain format the spec describes, so a hand-written
    list can be dropped in instead of the workbook.

``candidates.csv``
    email, registration_number, full_name, phone, programme -- richer, and what
    ``seed_db.py`` prefers when it is present.

Usage:
    python tools/build_candidates.py <Application_Details.xlsx> [seed_data_dir]
"""
from __future__ import annotations

import csv
import pathlib
import re
import sys
import unicodedata

REQUIRED = ("Email_ID",)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


def clean(value: object) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    return re.sub(r"\s+", " ", text).strip()


def title_name(*parts: str) -> str:
    joined = " ".join(p for p in parts if p)
    joined = re.sub(r"\s+", " ", joined).strip()
    # The registrar's export mixes ALL CAPS and Mixed Case; normalise, but
    # leave single-letter initials alone.
    return " ".join(w if len(w) == 1 else w.capitalize() for w in joined.split())


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    src = pathlib.Path(sys.argv[1])
    out_dir = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else pathlib.Path("seed_data")
    out_dir.mkdir(parents=True, exist_ok=True)

    import openpyxl

    wb = openpyxl.load_workbook(src, data_only=True)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))

    # The header is not necessarily row 1 -- find the row that carries it.
    header_idx = None
    for i, row in enumerate(rows[:25]):
        cells = [clean(c) for c in row]
        if all(any(c == req for c in cells) for req in REQUIRED):
            header_idx = i
            break
    if header_idx is None:
        print(f"ERROR: no header row containing {REQUIRED} found in {src}", file=sys.stderr)
        return 1

    header = [clean(c) for c in rows[header_idx]]
    idx = {name: i for i, name in enumerate(header) if name}

    def cell(row: tuple, name: str) -> str:
        i = idx.get(name)
        return clean(row[i]) if i is not None and i < len(row) else ""

    seen: set[str] = set()
    records: list[dict[str, str]] = []
    skipped: list[str] = []

    for row in rows[header_idx + 1 :]:
        email = cell(row, "Email_ID").lower()
        if not email:
            continue
        if not EMAIL_RE.match(email):
            skipped.append(f"invalid email: {email!r}")
            continue
        if email in seen:
            skipped.append(f"duplicate email: {email}")
            continue
        seen.add(email)

        serial = cell(row, "S.No") or str(len(records) + 1)
        full_name = title_name(
            cell(row, "First_Name"), cell(row, "Middle_Name"), cell(row, "Last_Name")
        ) or email.split("@")[0]
        records.append(
            {
                "email": email,
                "registration_number": f"DSIRT2026{int(float(serial)):04d}"
                if serial.replace(".", "", 1).isdigit()
                else f"DSIRT2026{len(records) + 1:04d}",
                "full_name": full_name,
                "phone": cell(row, "Mobile"),
                "programme": cell(row, "Program"),
            }
        )

    txt = out_dir / "candidates.txt"
    txt.write_text("\n".join(r["email"] for r in records) + "\n", encoding="utf-8")

    csv_path = out_dir / "candidates.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["email", "registration_number", "full_name", "phone", "programme"]
        )
        writer.writeheader()
        writer.writerows(records)

    print(f"Wrote {len(records)} candidates")
    print(f"  {txt}")
    print(f"  {csv_path}")
    for note in skipped:
        print(f"  skipped: {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
