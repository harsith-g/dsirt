"""Adapter for the compact question-paper layout.

The corrected papers ship as:

    question_number,question_text,option_a,option_b,option_c,option_d,correct_answer

which is everything specific to the question and nothing about where it
belongs. Phase, domain, marks and negative marks are properties of the *paper*,
not the row, so they are supplied from the manifest (when seeding) or chosen by
the administrator (when uploading through the admin UI) and merged in here.

Rows whose ``correct_answer`` is blank are separated out rather than defaulted.
An unkeyed question that reached the bank would mark every candidate wrong, so
it is withheld and reported by question number.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.services.text_utils import normalise_text

# The minimum set that identifies this layout.
SIMPLE_REQUIRED = ("question_text", "option_a", "option_b", "correct_answer")
# Present in the supplied files; not required, but recognised.
SIMPLE_OPTIONAL = ("question_number", "option_c", "option_d", "option_e", "option_f")


@dataclass(slots=True)
class AdaptReport:
    total: int = 0
    usable: int = 0
    unkeyed: list[str] = field(default_factory=list)
    empty_text: list[str] = field(default_factory=list)
    too_few_options: list[str] = field(default_factory=list)
    unresolvable_key: list[str] = field(default_factory=list)

    @property
    def withheld(self) -> int:
        return (
            len(self.unkeyed)
            + len(self.empty_text)
            + len(self.too_few_options)
            + len(self.unresolvable_key)
        )

    def notes(self) -> list[str]:
        out: list[str] = []
        if self.unkeyed:
            out.append(
                f"no answer key for {len(self.unkeyed)} question(s): "
                + _compress(self.unkeyed)
            )
        if self.unresolvable_key:
            out.append(
                f"answer key matches no option for {len(self.unresolvable_key)} "
                f"question(s): " + _compress(self.unresolvable_key)
            )
        if self.empty_text:
            out.append(f"blank question text: {_compress(self.empty_text)}")
        if self.too_few_options:
            out.append(f"fewer than 2 options: {_compress(self.too_few_options)}")
        return out


def _compress(numbers: list[str]) -> str:
    """Render [1,2,3,7,8,40] as '1-3, 7-8, 40' so a 50-item list stays readable."""
    try:
        ints = sorted(int(n) for n in numbers)
    except (TypeError, ValueError):
        return ", ".join(numbers[:12]) + (" ..." if len(numbers) > 12 else "")
    if not ints:
        return ""
    spans: list[str] = []
    start = prev = ints[0]
    for n in ints[1:]:
        if n == prev + 1:
            prev = n
            continue
        spans.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = n
    spans.append(str(start) if start == prev else f"{start}-{prev}")
    return ", ".join(spans)


def _answer_resolves(answer: str, options: dict[str, str]) -> bool:
    """Would the strict importer be able to turn this key into an option?

    Mirrors the importer's own precedence: option label, then exact option
    text, then 1-based position. Deliberately conservative -- anything it
    cannot place is withheld rather than guessed.
    """
    labels = {label.upper() for label in options}
    for token in answer.replace("/", ",").replace(";", ",").split(","):
        token = token.strip().strip(".)").strip()
        if not token:
            continue
        if token.upper() in labels:
            continue
        if any(value.casefold() == token.casefold() for value in options.values()):
            continue
        if token.isdigit() and 1 <= int(token) <= len(options):
            continue
        return False
    return True


def looks_like_simple_schema(rows: list[dict[str, str]]) -> bool:
    if not rows:
        return False
    present = set(rows[0].keys())
    if "phase" in present:
        return False  # already the strict schema
    return all(column in present for column in SIMPLE_REQUIRED)


def adapt(
    rows: list[dict[str, str]],
    *,
    phase: str,
    domain_code: str | None,
    marks: float = 1.0,
    negative_marks: float = 0.0,
    question_type: str = "SINGLE_CHOICE",
    difficulty: str = "MEDIUM",
    source_prefix: str = "",
) -> tuple[list[dict[str, str]], AdaptReport]:
    """Expand compact rows into strict-importer rows.

    Returns ``(rows, report)``. Only rows carrying a usable answer key come
    back; the rest are listed in the report by question number.
    """
    report = AdaptReport(total=len(rows))
    out: list[dict[str, str]] = []

    for index, row in enumerate(rows, start=1):
        number = normalise_text(str(row.get("question_number") or "")) or str(index)
        text = normalise_text(row.get("question_text") or "")

        options: dict[str, str] = {}
        for label in ("a", "b", "c", "d", "e", "f"):
            value = normalise_text(row.get(f"option_{label}") or "")
            if value:
                options[label] = value

        if not text:
            report.empty_text.append(number)
            continue
        if len(options) < 2:
            report.too_few_options.append(number)
            continue

        answer = normalise_text(row.get("correct_answer") or "")
        if not answer:
            # Withheld on purpose. Defaulting to "A" here would silently
            # publish a wrong key for every one of these questions.
            report.unkeyed.append(number)
            continue

        # The key must point at an option that actually exists on this row.
        # A key that does not resolve usually means the row was shredded by a
        # quoting error upstream, so the whole row is suspect -- withhold it
        # and name it, rather than failing the other 49 questions with it.
        if not _answer_resolves(answer, options):
            report.unresolvable_key.append(number)
            continue

        adapted: dict[str, str] = {
            "phase": phase,
            "domain_code": domain_code or "",
            "question_type": question_type,
            "question_text": text,
            "correct_answer": answer,
            "marks": str(marks),
            "negative_marks": str(negative_marks),
            "difficulty": difficulty,
            "explanation": normalise_text(row.get("explanation") or ""),
            "source_ref": f"{source_prefix}Q{number}" if source_prefix else f"Q{number}",
        }
        for label in ("a", "b", "c", "d", "e", "f"):
            adapted[f"option_{label}"] = options.get(label, "")
        out.append(adapted)

    report.usable = len(out)
    return out, report
