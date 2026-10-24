"""Bulk question-bank import (CSV + XLSX).

Two-pass and transactional:

* **Pass 1** parses and validates every row without touching the database.
  Foreign keys (``domain_code``), enum values, numeric ranges, option counts
  and answer-key references are all checked. Nothing is written.
* **Pass 2** runs only if pass 1 produced zero errors, and writes inside a
  single SAVEPOINT. Any failure rolls the whole import back.

Duplicate protection is an MD5 over the *normalised* question text (collapsed
whitespace, casefolded), compared against both the existing bank and the rows
already seen in this file.

The format is sniffed from the file's magic bytes, not its extension --
several of the real papers arrive as XLSX named ``.csv``.
"""
from __future__ import annotations

import csv
import io
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import Difficulty, Phase, QuestionType
from app.services.text_utils import normalise_text, question_md5

REQUIRED_COLUMNS = (
    "phase",
    "domain_code",
    "question_type",
    "question_text",
    "option_a",
    "option_b",
    "option_c",
    "option_d",
    "correct_answer",
    "marks",
    "negative_marks",
)
OPTIONAL_COLUMNS = ("difficulty", "explanation", "source_ref")
OPTION_LABELS = ("A", "B", "C", "D", "E", "F")

_XLSX_MAGIC = b"PK\x03\x04"


# ------------------------------------------------------------------ results
@dataclass(slots=True)
class RowError:
    row: int
    column: str | None
    message: str

    def __str__(self) -> str:
        where = f"row {self.row}"
        if self.column:
            where += f", column '{self.column}'"
        return f"{where}: {self.message}"


@dataclass(slots=True)
class ParsedQuestion:
    row: int
    phase: Phase
    domain_code: str | None
    question_type: QuestionType
    question_text: str
    options: list[tuple[str, str, bool]]  # (label, text, is_correct)
    marks: float
    negative_marks: float
    difficulty: Difficulty
    explanation: str | None
    source_ref: str | None
    question_hash: str


@dataclass(slots=True)
class ImportReport:
    total_rows: int = 0
    imported: int = 0
    duplicates_in_db: int = 0
    duplicates_in_file: int = 0
    errors: list[RowError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "total_rows": self.total_rows,
            "imported": self.imported,
            "duplicates_in_db": self.duplicates_in_db,
            "duplicates_in_file": self.duplicates_in_file,
            "errors": [
                {"row": e.row, "column": e.column, "message": e.message} for e in self.errors
            ],
            "warnings": self.warnings,
        }


# ------------------------------------------------------------------ reading
def _read_xlsx_rows(data: bytes) -> list[dict[str, str]]:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    ws = wb.worksheets[0]
    rows = ws.iter_rows(values_only=True)
    try:
        header_row = next(rows)
    except StopIteration:
        return []
    headers = [normalise_text(str(h or "")).lower() for h in header_row]
    out: list[dict[str, str]] = []
    for raw in rows:
        if raw is None or not any(c is not None and str(c).strip() for c in raw):
            continue
        out.append(
            {
                headers[i]: ("" if raw[i] is None else str(raw[i]))
                for i in range(min(len(headers), len(raw)))
                if headers[i]
            }
        )
    wb.close()
    return out


def _detect_dialect(text: str) -> type[csv.Dialect]:
    """Work out the delimiter, and *only* the delimiter.

    `csv.Sniffer` also guesses quoting, and it guesses badly: on a file
    containing a doubled quote inside a quoted field -- for example a question
    whose text includes ``printf("%d", x);``, stored correctly as
    ``""%d""`` -- it reports ``doublequote=False``. Honouring that shreds the
    row across columns and silently mangles the question.

    So the sniffer's opinion on the delimiter is kept and everything else comes
    from ``csv.excel``, which is what wrote these files in the first place.
    """
    sample = text[:16384]
    delimiter = ","
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        # Fall back to whichever candidate appears most often in the header.
        header = sample.splitlines()[0] if sample.splitlines() else ""
        delimiter = max(",;\t|", key=header.count) if header else ","

    class _Dialect(csv.excel):
        pass

    _Dialect.delimiter = delimiter
    return _Dialect


def _read_csv_rows(data: bytes) -> list[dict[str, str]]:
    # utf-8-sig strips the BOM Excel writes, which otherwise turns the first
    # header into '﻿phase' and fails the required-column check.
    for encoding in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    else:  # pragma: no cover
        text = data.decode("utf-8", errors="replace")

    reader = csv.DictReader(io.StringIO(text), dialect=_detect_dialect(text))
    out: list[dict[str, str]] = []
    for raw in reader:
        cleaned = {
            normalise_text(str(k or "")).lower(): ("" if v is None else str(v))
            for k, v in raw.items()
            if k is not None
        }
        if any(v.strip() for v in cleaned.values()):
            out.append(cleaned)
    return out


def read_tabular(data: bytes) -> list[dict[str, str]]:
    """Sniff XLSX vs CSV by magic bytes rather than trusting the extension."""
    if data[:4] == _XLSX_MAGIC:
        return _read_xlsx_rows(data)
    return _read_csv_rows(data)


# ----------------------------------------------------------------- parsing
def _parse_correct_answer(
    raw: str, options: list[tuple[str, str]], qtype: QuestionType
) -> tuple[set[str], str | None]:
    """Accept ``B``, ``b``, ``B,D``, ``2``, or the literal option text.

    Precedence matters, because on a numeric paper a token like ``4`` can
    plausibly mean "option 4" or "the option whose text is 4":

    1. an option label -- unambiguous;
    2. an exact option text match -- strong evidence;
    3. a 1-based position -- weakest.

    If a numeric token matches an option's text *and* a position, and the two
    disagree, the row is rejected rather than guessed. Silently picking one
    would mis-key a question and mark every correct candidate wrong.
    """
    raw = normalise_text(raw)
    if not raw:
        return set(), "correct_answer is empty"

    labels = {label for label, _ in options}
    by_text = {normalise_text(text).casefold(): label for label, text in options}
    picked: set[str] = set()

    for token in re.split(r"[,/;|]| and ", raw):
        token = token.strip().strip(".)").strip()
        if not token:
            continue
        upper = token.upper()
        if upper in labels:
            picked.add(upper)
            continue

        text_match = by_text.get(token.casefold())
        if token.isdigit():
            idx = int(token) - 1
            position_match = options[idx][0] if 0 <= idx < len(options) else None
            if text_match and position_match and text_match != position_match:
                return set(), (
                    f"correct_answer '{token}' is ambiguous: it matches the text of "
                    f"option {text_match} and the position of option {position_match}. "
                    f"Use the option letter instead."
                )
            resolved = text_match or position_match
            if resolved:
                picked.add(resolved)
                continue
            return set(), f"correct_answer '{token}' is out of range"

        if text_match:
            picked.add(text_match)
            continue
        if qtype is QuestionType.TRUE_FALSE and upper in ("TRUE", "FALSE", "T", "F"):
            want = "TRUE" if upper in ("TRUE", "T") else "FALSE"
            for label, text in options:
                if normalise_text(text).upper() == want:
                    picked.add(label)
                    break
            else:
                return set(), f"correct_answer '{token}' matches no option"
            continue
        return set(), f"correct_answer '{token}' matches no option label or text"

    if not picked:
        return set(), "correct_answer could not be resolved"
    if qtype in (QuestionType.SINGLE_CHOICE, QuestionType.TRUE_FALSE) and len(picked) > 1:
        return set(), f"{qtype.value} accepts exactly one correct answer, got {len(picked)}"
    return picked, None


def _parse_enum(value: str, enum_cls, default=None, *, field_name: str):
    value = normalise_text(value).upper().replace(" ", "_").replace("-", "_")
    if not value:
        if default is not None:
            return default, None
        return None, f"{field_name} is required"
    try:
        return enum_cls(value), None
    except ValueError:
        allowed = ", ".join(e.value for e in enum_cls)
        return None, f"{field_name} '{value}' is invalid (expected one of: {allowed})"


def _parse_float(value: str, field_name: str, default: float, minimum: float) -> tuple[float, str | None]:
    value = normalise_text(value)
    if not value:
        return default, None
    try:
        parsed = float(value)
    except ValueError:
        return default, f"{field_name} '{value}' is not a number"
    if parsed < minimum:
        return default, f"{field_name} must be >= {minimum}"
    return parsed, None


def parse_rows(
    rows: Sequence[dict[str, str]], known_domain_codes: set[str]
) -> tuple[list[ParsedQuestion], ImportReport]:
    """Pass 1: validate everything, write nothing."""
    report = ImportReport(total_rows=len(rows))
    parsed: list[ParsedQuestion] = []
    seen_hashes: dict[str, int] = {}

    if not rows:
        report.errors.append(RowError(0, None, "The file contains no data rows."))
        return parsed, report

    present = set(rows[0].keys())
    missing = [c for c in REQUIRED_COLUMNS if c not in present]
    if missing:
        report.errors.append(
            RowError(
                1,
                None,
                "Missing required column(s): "
                + ", ".join(missing)
                + f". Found: {', '.join(sorted(present)) or '(none)'}",
            )
        )
        return parsed, report

    for offset, row in enumerate(rows):
        # +2: one for the header line, one for 1-based spreadsheet numbering.
        line = offset + 2
        errors_before = len(report.errors)

        def err(msg: str, column: str | None = None) -> None:
            report.errors.append(RowError(line, column, msg))

        phase, e = _parse_enum(row.get("phase", ""), Phase, field_name="phase")
        if e:
            err(e, "phase")
        qtype, e = _parse_enum(
            row.get("question_type", ""),
            QuestionType,
            default=QuestionType.SINGLE_CHOICE,
            field_name="question_type",
        )
        if e:
            err(e, "question_type")
        difficulty, e = _parse_enum(
            row.get("difficulty", ""),
            Difficulty,
            default=Difficulty.MEDIUM,
            field_name="difficulty",
        )
        if e:
            err(e, "difficulty")

        domain_code = normalise_text(row.get("domain_code", "")).upper() or None
        if phase is Phase.PHASE_1 and domain_code:
            err("Phase 1 questions must not carry a domain_code", "domain_code")
        elif phase is Phase.PHASE_2:
            if not domain_code:
                err("Phase 2 questions require a domain_code", "domain_code")
            elif domain_code not in known_domain_codes:
                err(
                    f"domain_code '{domain_code}' does not exist "
                    f"(known: {', '.join(sorted(known_domain_codes)) or 'none'})",
                    "domain_code",
                )

        question_text = normalise_text(row.get("question_text", ""))
        if not question_text:
            err("question_text is empty", "question_text")
        elif len(question_text) < 5:
            err("question_text is too short to be a real question", "question_text")

        options: list[tuple[str, str]] = []
        for label in OPTION_LABELS:
            text = normalise_text(row.get(f"option_{label.lower()}", ""))
            if text:
                options.append((label, text))
        if qtype is not QuestionType.TEXT and len(options) < 2:
            err(
                f"at least 2 options are required, found {len(options)}",
                "option_a",
            )
        labels_seen = [t.casefold() for _, t in options]
        if len(set(labels_seen)) != len(labels_seen):
            err("duplicate option text within the same question", "option_a")

        correct: set[str] = set()
        if qtype is not QuestionType.TEXT and options:
            correct, e = _parse_correct_answer(row.get("correct_answer", ""), options, qtype)
            if e:
                err(e, "correct_answer")

        marks, e = _parse_float(row.get("marks", ""), "marks", 1.0, 0.0001)
        if e:
            err(e, "marks")
        negative, e = _parse_float(row.get("negative_marks", ""), "negative_marks", 0.0, 0.0)
        if e:
            err(e, "negative_marks")

        if len(report.errors) > errors_before:
            continue

        qhash = question_md5(question_text)
        if qhash in seen_hashes:
            report.duplicates_in_file += 1
            report.warnings.append(
                f"row {line}: duplicate of row {seen_hashes[qhash]} in this file -- skipped"
            )
            continue
        seen_hashes[qhash] = line

        parsed.append(
            ParsedQuestion(
                row=line,
                phase=phase,
                domain_code=domain_code,
                question_type=qtype,
                question_text=question_text,
                options=[(lbl, txt, lbl in correct) for lbl, txt in options],
                marks=marks,
                negative_marks=negative,
                difficulty=difficulty,
                explanation=normalise_text(row.get("explanation", "")) or None,
                source_ref=normalise_text(row.get("source_ref", "")) or None,
                question_hash=qhash,
            )
        )

    if report.errors:
        # All-or-nothing: if any row failed validation, hand back no rows at
        # all, so a caller that forgets to check `report.ok` still cannot
        # write a partial import.
        return [], report
    return parsed, report


# ------------------------------------------------------------------ writing
async def persist(
    db: AsyncSession,
    parsed: Iterable[ParsedQuestion],
    report: ImportReport,
    domain_ids: dict[str, uuid.UUID],
) -> ImportReport:
    """Pass 2: single SAVEPOINT; all rows land or none do."""
    # Imported here, not at module scope, so the validation half of this module
    # stays importable without the ORM -- the offline paper-inspection tools and
    # the database-free tests depend on that.
    from app.models import Question, QuestionOption

    parsed = list(parsed)
    if not parsed:
        return report

    hashes = {p.question_hash for p in parsed}
    existing = {
        (h, ph, d)
        for h, ph, d in (
            await db.execute(
                select(Question.question_hash, Question.phase, Question.domain_id).where(
                    Question.question_hash.in_(hashes)
                )
            )
        ).all()
    }

    async with db.begin_nested():
        for p in parsed:
            domain_id = domain_ids.get(p.domain_code) if p.domain_code else None
            if (p.question_hash, p.phase, domain_id) in existing:
                report.duplicates_in_db += 1
                report.warnings.append(
                    f"row {p.row}: already present in the question bank -- skipped"
                )
                continue

            question = Question(
                phase=p.phase,
                domain_id=domain_id,
                question_type=p.question_type,
                question_text=p.question_text,
                question_hash=p.question_hash,
                marks=p.marks,
                negative_marks=p.negative_marks,
                difficulty=p.difficulty,
                explanation=p.explanation,
                source_ref=p.source_ref,
                is_active=True,
            )
            db.add(question)
            await db.flush()
            for order, (label, text, is_correct) in enumerate(p.options):
                db.add(
                    QuestionOption(
                        question_id=question.id,
                        option_label=label,
                        option_text=text,
                        is_correct=is_correct,
                        display_order=order,
                    )
                )
            existing.add((p.question_hash, p.phase, domain_id))
            report.imported += 1
        await db.flush()
    return report


async def import_bytes(
    db: AsyncSession,
    data: bytes,
    *,
    phase: str | None = None,
    domain_code: str | None = None,
    marks: float = 1.0,
    negative_marks: float = 0.0,
) -> ImportReport:
    """Full two-pass import. Commits nothing -- the caller owns the transaction.

    Accepts two layouts:

    * the **strict schema**, which carries ``phase`` and ``domain_code`` per row;
    * the **compact schema** used by the supplied papers
      (``question_number, question_text, option_a..d, correct_answer``), which
      carries no placement information -- so ``phase`` must be passed in, and
      ``domain_code`` too for Phase 2.
    """
    from app.models import Domain
    from app.services.simple_schema import adapt, looks_like_simple_schema

    domains = (await db.execute(select(Domain))).scalars().all()
    domain_ids = {d.code.upper(): d.id for d in domains}

    try:
        rows = read_tabular(data)
    except Exception as exc:  # malformed workbook, bad zip, etc.
        report = ImportReport()
        report.errors.append(RowError(0, None, f"Could not read the file: {exc}"))
        return report

    if looks_like_simple_schema(rows):
        if not phase:
            report = ImportReport(total_rows=len(rows))
            report.errors.append(
                RowError(
                    1,
                    None,
                    "This file uses the compact layout (question_number, question_text, "
                    "option_a-d, correct_answer), which does not say which phase or "
                    "domain the questions belong to. Choose a phase and domain on the "
                    "import form, or use the full template.",
                )
            )
            return report
        rows, adapt_report = adapt(
            rows,
            phase=phase,
            domain_code=domain_code,
            marks=marks,
            negative_marks=negative_marks,
        )
        parsed, report = parse_rows(rows, set(domain_ids))
        report.total_rows = adapt_report.total
        # Surface what the adapter withheld, so a half-keyed paper is visible
        # rather than quietly short.
        report.warnings.extend(adapt_report.notes())
        if not report.ok:
            return report
        return await persist(db, parsed, report, domain_ids)

    parsed, report = parse_rows(rows, set(domain_ids))
    if not report.ok:
        return report
    return await persist(db, parsed, report, domain_ids)


def build_template_csv() -> str:
    """The exact header the importer expects, plus two worked examples."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(list(REQUIRED_COLUMNS) + list(OPTIONAL_COLUMNS))
    writer.writerow(
        [
            "PHASE_1", "", "SINGLE_CHOICE", "The first step in the research process is generally to:",
            "Analyse data", "Identify and define the research problem", "Write the conclusion",
            "Select a journal", "B", "1", "0", "EASY", "", "RM Set-II Q1",
        ]
    )
    writer.writerow(
        [
            "PHASE_2", "CS", "SINGLE_CHOICE",
            "A network uses IPv4 with subnet mask 255.255.255.224. How many usable host addresses are available in each subnet?",
            "30", "32", "62", "126", "A", "1", "0.25", "MEDIUM", "2^5 - 2 = 30", "CS Q2",
        ]
    )
    return buf.getvalue()
