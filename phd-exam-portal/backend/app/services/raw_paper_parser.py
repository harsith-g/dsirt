"""Parser for legacy, human-formatted question papers.

The supplied papers are not in the strict import schema -- they are raw
exports of Word/Excel question papers. Between the ten files there are four
different answer-key layouts and a good deal of damage from earlier format
conversions. This module turns them into rows the strict importer understands,
and reports precisely what it had to reject rather than guessing.

It never invents an answer. A question with no resolvable key is reported and
withheld, not given a plausible-looking one.

Layouts handled
---------------
Questions
    ``1. text`` / ``1) text`` followed by options on one line
    (``A. x   B. y   C. z   D. w``), on several lines, or one per line
    (``a) x``). Option labels must appear in sequence (A, B, C, ...) which is
    what keeps an inline "B." inside prose from being mistaken for an option.

Answer keys
    1. Vertical blob in one cell: ``Q.No. / Answer / 1 / C / 11 / C / ...``
    2. One token per spreadsheet cell: ``"1","a","7","b", ...``
    3. Inline runs: ``1. B   2. B   3. A   4. B``
    4. Grid rows in a worksheet: ``| 1 | A | 11 | B | 21 | A |``

Layouts 1, 2 and 4 all reduce to the same ordered token stream, so they share
one pairing pass; layout 3 is detected first by regex.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field

from app.services.text_utils import normalise_text

_XLSX_MAGIC = b"PK\x03\x04"

# "1." / "1)" / "1 ." at the start of a line.
_Q_START = re.compile(r"^\s*(\d{1,3})\s*[.)]\s*(.*)$")
# An answer-key heading, in any of the spellings the papers use.
_ANSWER_KEY_HEADING = re.compile(r"answer\s*keys?\b", re.IGNORECASE)
# Inline key runs: "1. B   2. B" (layout 3).
_INLINE_KEY = re.compile(r"(?<!\d)(\d{1,3})\s*[.)]\s*([A-Fa-f])(?![A-Za-z0-9])")
_SINGLE_LETTER = re.compile(r"^[A-Fa-f]$")
_HEADER_NOISE = re.compile(
    r"^(q\.?\s*no\.?|q\.?\s*n\.?|q\.?|answers?|ans\.?|s\.?\s*no\.?)$", re.IGNORECASE
)
# Lines that are paper furniture rather than content.
_BOILERPLATE = re.compile(
    r"^(dhanalakshmi|\(deemed|perambalur|school of|department of|entrance exam|"
    r"ph\.?d\.?\s|dsirt|paper\s*-?\s*\d|total questions|type\s*:|instructions?|"
    r"maximum marks|answer all|each question|choose the most|there is no negative|"
    r"multiple choice|questions are arranged|time\s*:|number of questions|"
    r"set\s*-\s*[ivx]+$)",
    re.IGNORECASE,
)
# Subject-section headings such as "A. Computer Networks" or "G. Artificial
# Intelligence". These are lexically identical to an option line ("A. Flow
# control using the receiver window"), so the pattern alone cannot separate
# them -- it is only ever consulted for a label that does NOT continue the
# expected option sequence. See `_looks_like_section_heading`.
_SECTION_HEADING = re.compile(r"^[A-J][.)]\s+[A-Z][A-Za-z&/ -]{3,60}$")


@dataclass(slots=True)
class RawQuestion:
    number: int
    text: str
    options: list[tuple[str, str]] = field(default_factory=list)  # (label, text)
    correct_label: str | None = None

    @property
    def labels(self) -> list[str]:
        return [lbl for lbl, _ in self.options]


@dataclass(slots=True)
class PaperReport:
    source: str
    parsed: int = 0
    accepted: int = 0
    rejected_no_options: list[int] = field(default_factory=list)
    rejected_few_options: list[int] = field(default_factory=list)
    rejected_no_key: list[int] = field(default_factory=list)
    rejected_bad_key: list[int] = field(default_factory=list)
    missing_question_text: list[int] = field(default_factory=list)
    key_entries: int = 0
    key_conflicts: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        bits = [f"{self.source}: {self.accepted}/{self.parsed} accepted"]
        if self.rejected_no_options:
            bits.append(f"no options {self.rejected_no_options}")
        if self.rejected_few_options:
            bits.append(f"<2 options {self.rejected_few_options}")
        if self.rejected_no_key:
            bits.append(f"no answer key {self.rejected_no_key}")
        if self.rejected_bad_key:
            bits.append(f"key points at a missing option {self.rejected_bad_key}")
        if self.missing_question_text:
            bits.append(f"orphan option block near {self.missing_question_text}")
        return "; ".join(bits)


# --------------------------------------------------------------- text intake
def _xlsx_to_lines(data: bytes) -> list[str]:
    """Flatten a worksheet to lines.

    A row with one populated cell contributes that cell's (possibly multiline)
    text. A row with several populated cells contributes one line per cell,
    which is exactly what the grid-style answer keys need.
    """
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True)
    lines: list[str] = []
    for ws in wb.worksheets:
        for row in ws.iter_rows(values_only=True):
            cells = [str(c) for c in row if c is not None and str(c).strip()]
            if not cells:
                continue
            for cell in cells:
                lines.extend(cell.splitlines() or [cell])
    wb.close()
    return lines


def _csv_to_lines(data: bytes) -> list[str]:
    import csv

    for encoding in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            text = data.decode(encoding)
            break
        except (UnicodeDecodeError, UnicodeError):
            continue
    else:  # pragma: no cover
        text = data.decode("utf-8", errors="replace")

    lines: list[str] = []
    for row in csv.reader(io.StringIO(text)):
        for cell in row:
            if cell and cell.strip():
                lines.extend(cell.splitlines() or [cell])
    return lines


def to_lines(data: bytes) -> list[str]:
    return _xlsx_to_lines(data) if data[:4] == _XLSX_MAGIC else _csv_to_lines(data)


# ------------------------------------------------------------ option finding
def _next_label(current: str | None) -> str:
    return "A" if current is None else chr(ord(current) + 1)


def _split_option_line(line: str, expected: str) -> list[tuple[str, str]]:
    """Pull ``A. x  B. y`` style runs out of one line.

    Only labels that continue the expected sequence are honoured, so an
    abbreviation like "B." inside an option's prose is not mistaken for the
    start of the next option.

    The separator after the label is ``\\s*`` rather than ``\\s+`` because
    several papers contain "c.5,6 &10" with no space; sequence enforcement is
    what keeps that permissiveness safe.
    """
    found: list[tuple[int, str]] = []
    want = expected
    for m in re.finditer(r"(?<![A-Za-z0-9])([A-Fa-f])\s*[.)]\s*", line):
        label = m.group(1).upper()
        if label != want:
            continue
        # A mid-line label must be preceded by whitespace, not glued to prose.
        if m.start() > 0 and not line[m.start() - 1].isspace():
            continue
        found.append((m.start(), label))
        want = _next_label(want)

    if not found:
        return []
    out: list[tuple[str, str]] = []
    for i, (pos, label) in enumerate(found):
        end = found[i + 1][0] if i + 1 < len(found) else len(line)
        body = line[pos:end]
        body = re.sub(r"^\s*[A-Fa-f]\s*[.)]\s*", "", body)
        out.append((label, normalise_text(body)))
    return out


def _is_noise(line: str) -> bool:
    """Paper furniture: letterheads, rubrics, blank separators.

    Deliberately does *not* test `_SECTION_HEADING` -- that check is
    sequence-aware and lives in `_looks_like_section_heading`.
    """
    stripped = normalise_text(line)
    if not stripped or stripped in {".", "-", "_"}:
        return True
    if _BOILERPLATE.match(stripped):
        return True
    return False


def _looks_like_section_heading(line: str, expected: str | None) -> bool:
    """Decide between "A. Computer Networks" (a heading) and
    "A. Flow control using the receiver window" (option A).

    The two are lexically indistinguishable, so the only reliable signal is
    position in the option sequence: a labelled line whose label *is* the one
    we are waiting for is an option, full stop. Anything else that still looks
    like a title is treated as a section heading and dropped.
    """
    stripped = normalise_text(line)
    m = re.match(r"^([A-Ja-j])[.)]\s+", stripped)
    if not m:
        return False
    if expected is not None and m.group(1).upper() == expected:
        return False  # it continues the sequence -- it is an option
    return bool(_SECTION_HEADING.match(stripped))


# ------------------------------------------------------------- key extraction
def _pair_tokens(lines: list[str]) -> tuple[dict[int, str], list[str]]:
    """Layouts 1, 2 and 4: an ordered stream of numbers and single letters."""
    key: dict[int, str] = {}
    conflicts: list[str] = []
    pending: int | None = None
    for raw in lines:
        for token in raw.splitlines():
            token = normalise_text(token)
            if not token or _HEADER_NOISE.match(token):
                continue
            if token.isdigit():
                n = int(token)
                if 1 <= n <= 999:
                    pending = n
                continue
            if _SINGLE_LETTER.match(token) and pending is not None:
                letter = token.upper()
                if pending in key and key[pending] != letter:
                    conflicts.append(f"Q{pending}: {key[pending]} vs {letter}")
                else:
                    key[pending] = letter
                pending = None
                continue
            # Anything else resets the pairing so stray prose cannot bind.
            pending = None
    return key, conflicts


def _is_bare_token(line: str) -> bool:
    """A cell holding only a question number, an option letter or a column
    header -- the building blocks of every grid-style answer key."""
    t = normalise_text(line)
    if not t:
        return True
    return bool(t.isdigit() or _SINGLE_LETTER.match(t) or _HEADER_NOISE.match(t))


def _find_key_region(lines: list[str]) -> int:
    """Locate where the answer key starts.

    Preferred signal is an explicit "Answer Key" heading. Several papers have
    none and simply run into a ``Q | Ans`` grid, so the fallback looks for a
    long trailing region built almost entirely out of bare tokens -- prose
    cannot survive that test, and a real key cannot fail it.
    """
    for i, line in enumerate(lines):
        if _ANSWER_KEY_HEADING.search(line or ""):
            return i

    best = len(lines)
    run_start: int | None = None
    non_bare = 0
    for i in range(len(lines) - 1, -1, -1):
        if _is_bare_token(lines[i]):
            run_start = i
            non_bare = 0
        else:
            non_bare += 1
            # Tolerate a couple of stray captions inside the grid, then stop.
            if non_bare > 2:
                break
    if run_start is not None and (len(lines) - run_start) >= 40:
        # Require the region to actually resolve into key pairs.
        pairs, _ = _pair_tokens(lines[run_start:])
        if len(pairs) >= 20:
            best = run_start
    return best


def extract_answer_key(lines: list[str]) -> tuple[dict[int, str], list[str], int]:
    """Return ``(key, conflicts, heading_index)``.

    ``heading_index`` is where the answer-key section starts, so the question
    parser can stop before it and not read "1. B" as question 1.
    """
    heading_index = _find_key_region(lines)
    if heading_index >= len(lines):
        return {}, [], len(lines)

    tail = lines[heading_index:]
    blob = "\n".join(tail)

    # Layout 3 -- inline runs. Needs a decent number of hits to win.
    inline = _INLINE_KEY.findall(blob)
    if len(inline) >= 20:
        key: dict[int, str] = {}
        conflicts: list[str] = []
        for num, letter in inline:
            n, l = int(num), letter.upper()
            if n in key and key[n] != l:
                conflicts.append(f"Q{n}: {key[n]} vs {l}")
            else:
                key[n] = l
        return key, conflicts, heading_index

    key, conflicts = _pair_tokens(tail)
    return key, conflicts, heading_index


# ---------------------------------------------------------------- main parse
def parse_paper(data: bytes, source: str) -> tuple[list[RawQuestion], PaperReport]:
    report = PaperReport(source=source)
    lines = to_lines(data)
    key, conflicts, heading_index = extract_answer_key(lines)
    report.key_entries = len(key)
    report.key_conflicts = conflicts
    if not key:
        report.notes.append(
            "No answer key found in this file. Questions are parsed but withheld."
        )

    body = lines[:heading_index]
    questions: list[RawQuestion] = []
    current: RawQuestion | None = None
    expected_label: str | None = None
    orphan_reported = False

    for raw in body:
        line = (raw or "").rstrip()
        if _is_noise(line):
            continue
        stripped = normalise_text(line)

        m = _Q_START.match(line)
        # A new question must advance the numbering; this rejects lines like
        # "2. Which..." appearing inside an option's text.
        if m and (
            current is None
            or int(m.group(1)) > current.number
            or not questions
        ):
            number = int(m.group(1))
            text = normalise_text(m.group(2))
            if current is not None:
                questions.append(current)
            current = RawQuestion(number=number, text=text)
            expected_label = "A"
            continue

        want = expected_label or "A"
        if _looks_like_section_heading(line, expected_label if current else None):
            continue
        opts = _split_option_line(line, want)
        if opts:
            if current is None:
                # Option block with no question stem -- the stem was lost in an
                # earlier conversion of this paper.
                if not orphan_reported:
                    report.missing_question_text.append(
                        (questions[-1].number + 1) if questions else 1
                    )
                    orphan_reported = True
                continue
            current.options.extend(opts)
            expected_label = _next_label(opts[-1][0])
            orphan_reported = False
            continue

        # Continuation text.
        if current is not None:
            if current.options:
                # Trailing text after options belongs to the last option.
                label, existing = current.options[-1]
                current.options[-1] = (label, normalise_text(f"{existing} {stripped}"))
            elif current.text:
                current.text = normalise_text(f"{current.text} {stripped}")
            else:
                current.text = stripped

    if current is not None:
        questions.append(current)

    report.parsed = len(questions)

    accepted: list[RawQuestion] = []
    for q in questions:
        if not q.text:
            report.missing_question_text.append(q.number)
            continue
        if not q.options:
            report.rejected_no_options.append(q.number)
            continue
        if len(q.options) < 2:
            report.rejected_few_options.append(q.number)
            continue
        # Drop options whose text was destroyed by a bad conversion (maths
        # symbols and Tamil script are the usual casualties).
        q.options = [(lbl, txt) for lbl, txt in q.options if txt and txt.strip(" ?.")]
        if len(q.options) < 2:
            report.rejected_few_options.append(q.number)
            continue

        letter = key.get(q.number)
        if letter is None:
            report.rejected_no_key.append(q.number)
            continue
        if letter not in q.labels:
            report.rejected_bad_key.append(q.number)
            continue
        q.correct_label = letter
        accepted.append(q)

    report.accepted = len(accepted)
    return accepted, report


def to_import_rows(
    questions: list[RawQuestion],
    *,
    phase: str,
    domain_code: str | None,
    marks: float = 1.0,
    negative_marks: float = 0.0,
    source_prefix: str = "",
) -> list[dict[str, str]]:
    """Convert accepted questions into strict-importer rows."""
    rows: list[dict[str, str]] = []
    for q in questions:
        row: dict[str, str] = {
            "phase": phase,
            "domain_code": domain_code or "",
            "question_type": "SINGLE_CHOICE",
            "question_text": q.text,
            "correct_answer": q.correct_label or "",
            "marks": str(marks),
            "negative_marks": str(negative_marks),
            "difficulty": "MEDIUM",
            "explanation": "",
            "source_ref": f"{source_prefix}Q{q.number}" if source_prefix else f"Q{q.number}",
        }
        for label in ("a", "b", "c", "d", "e", "f"):
            row[f"option_{label}"] = ""
        for label, text in q.options:
            row[f"option_{label.lower()}"] = text
        rows.append(row)
    return rows
