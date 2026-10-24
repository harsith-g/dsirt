"""Tests for the compact-schema adapter and the CSV dialect handling.

The corrected papers arrive as
``question_number, question_text, option_a..d, correct_answer`` with no phase
or domain, so the adapter merges those in from the manifest. Its other job is
to withhold anything it cannot key, rather than defaulting.
"""
from __future__ import annotations

import csv
import io
import pathlib

import pytest

from app.services.question_importer import parse_rows, read_tabular
from app.services.simple_schema import (
    AdaptReport,
    adapt,
    looks_like_simple_schema,
)

SEED = pathlib.Path(__file__).resolve().parents[1] / "seed_data"

HEADER = [
    "question_number", "question_text",
    "option_a", "option_b", "option_c", "option_d", "correct_answer",
]


def compact(*rows: list[str]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(HEADER)
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().encode("utf-8")


def as_dicts(*rows: list[str]) -> list[dict[str, str]]:
    return [dict(zip(HEADER, row)) for row in rows]


# --------------------------------------------------------------- detection
def test_compact_layout_is_detected():
    assert looks_like_simple_schema(as_dicts(["1", "Q?", "a", "b", "c", "d", "B"]))


def test_strict_layout_is_not_treated_as_compact():
    rows = [{"phase": "PHASE_1", "question_text": "Q?", "option_a": "a",
             "option_b": "b", "correct_answer": "A"}]
    assert not looks_like_simple_schema(rows)


def test_empty_input_is_not_compact():
    assert not looks_like_simple_schema([])


# ----------------------------------------------------------------- adapting
def test_phase_and_domain_come_from_context():
    rows, report = adapt(
        as_dicts(["1", "What is 2+2?", "3", "4", "5", "6", "B"]),
        phase="PHASE_2", domain_code="CS", marks=2.0, negative_marks=0.5,
        source_prefix="CS-",
    )
    assert report.usable == 1
    row = rows[0]
    assert row["phase"] == "PHASE_2"
    assert row["domain_code"] == "CS"
    assert row["marks"] == "2.0"
    assert row["negative_marks"] == "0.5"
    assert row["source_ref"] == "CS-Q1"
    assert row["question_type"] == "SINGLE_CHOICE"


def test_adapted_rows_satisfy_the_strict_importer():
    rows, _ = adapt(
        as_dicts(
            ["1", "Q one?", "a", "b", "c", "d", "B"],
            ["2", "Q two?", "a", "b", "c", "d", "D"],
        ),
        phase="PHASE_2", domain_code="CS",
    )
    parsed, report = parse_rows(rows, {"CS"})
    assert report.ok, report.errors
    assert len(parsed) == 2
    assert [lbl for lbl, _, c in parsed[0].options if c] == ["B"]
    assert [lbl for lbl, _, c in parsed[1].options if c] == ["D"]


def test_phase1_rows_carry_no_domain():
    rows, _ = adapt(
        as_dicts(["1", "A Phase 1 question?", "a", "b", "c", "d", "A"]),
        phase="PHASE_1", domain_code=None,
    )
    assert rows[0]["domain_code"] == ""
    parsed, report = parse_rows(rows, {"CS"})
    assert report.ok, report.errors
    assert parsed[0].domain_code is None


# --------------------------------------------------- withholding, not guessing
def test_unkeyed_row_is_withheld_and_named():
    rows, report = adapt(
        as_dicts(
            ["1", "Keyed?", "a", "b", "c", "d", "B"],
            ["2", "Unkeyed?", "a", "b", "c", "d", ""],
        ),
        phase="PHASE_2", domain_code="CS",
    )
    assert len(rows) == 1
    assert report.unkeyed == ["2"]
    assert "no answer key" in report.notes()[0]


def test_a_fully_unkeyed_paper_yields_nothing():
    """The Tamil paper: readable text, no answer column filled in."""
    rows, report = adapt(
        as_dicts(*[[str(i), f"கேள்வி {i}?", "அ", "ஆ", "இ", "ஈ", ""] for i in range(1, 51)]),
        phase="PHASE_2", domain_code="TAM",
    )
    assert rows == []
    assert len(report.unkeyed) == 50
    assert "1-50" in report.notes()[0]


def test_key_matching_no_option_is_withheld_not_fatal():
    """One shredded row must not fail the other 49."""
    rows, report = adapt(
        as_dicts(
            ["1", "Fine?", "a", "b", "c", "d", "B"],
            ["2", "Shredded?", "trailing code", "rest of question", "0", "10", "20"],
            ["3", "Also fine?", "a", "b", "c", "d", "C"],
        ),
        phase="PHASE_2", domain_code="CS",
    )
    assert len(rows) == 2
    assert report.unresolvable_key == ["2"]
    parsed, import_report = parse_rows(rows, {"CS"})
    assert import_report.ok, import_report.errors


def test_blank_text_and_thin_options_are_withheld():
    rows, report = adapt(
        as_dicts(
            ["1", "", "a", "b", "c", "d", "A"],
            ["2", "Only one option?", "a", "", "", "", "A"],
        ),
        phase="PHASE_2", domain_code="CS",
    )
    assert rows == []
    assert report.empty_text == ["1"]
    assert report.too_few_options == ["2"]


def test_question_number_ranges_are_compressed_in_notes():
    report = AdaptReport(total=10, unkeyed=["1", "2", "3", "7", "9", "10"])
    assert "1-3, 7, 9-10" in report.notes()[0]


# ------------------------------------------------------------ csv dialect
def test_doubled_quotes_inside_a_field_survive():
    """Regression: csv.Sniffer reports doublequote=False on a file containing
    ``""%d""``, which shreds the row across columns. Only the delimiter may
    come from the sniffer."""
    data = compact(
        ["46",
         'Consider: int x = 10; void fun() { int x = 20; printf("%d", x); } '
         "Under static scoping, what value is printed?",
         "0", "10", "20", "Depends on the compiler", "C"]
    )
    rows = read_tabular(data)
    assert len(rows) == 1
    row = rows[0]
    assert 'printf("%d", x);' in row["question_text"]
    assert row["option_a"] == "0"
    assert row["option_c"] == "20"
    assert row["correct_answer"] == "C"


def test_commas_inside_quoted_fields_do_not_split_columns():
    data = compact(
        ["1", "Which list is right?", "Specific, Measurable, Achievable",
         "Simple, Modern", "c", "d", "A"]
    )
    row = read_tabular(data)[0]
    assert row["option_a"] == "Specific, Measurable, Achievable"
    assert row["correct_answer"] == "A"


def test_semicolon_delimited_file_is_still_read():
    text = ";".join(HEADER) + "\n" + ";".join(["1", "Q?", "a", "b", "c", "d", "B"]) + "\n"
    row = read_tabular(text.encode("utf-8"))[0]
    assert row["question_text"] == "Q?"
    assert row["correct_answer"] == "B"


# --------------------------------------------------------------- real files
@pytest.mark.parametrize(
    "filename,expected_usable,expected_withheld",
    [
        ("DSU_Research_Methodology_Set_II-_1_.csv", 50, 0),
        ("LIF-SCIENCE.csv", 50, 0),
        ("Pathology.csv", 50, 0),
        ("entomology.csv", 50, 0),
        ("PhD- Entrance  Eng QP.csv", 50, 0),
        ("MATHS.csv", 50, 0),
        ("AGRONOMY-Final.csv", 50, 0),
        ("PhD_Entrance_Exam_Commerce_Management_Alternate_Questions.csv", 50, 0),
        ("COMPUTER-SCIENCE-ENTRANCE-TEST-QUESTIONS-_1_.csv", 50, 0),
        ("Ph.D-ENTRANCE-EXAM-TAMILTAMILQuestion-_1_.csv", 0, 50),
    ],
)
def test_supplied_papers_adapt_as_expected(filename, expected_usable, expected_withheld):
    path = SEED / filename
    if not path.exists():
        pytest.skip(f"{filename} not present")
    rows = read_tabular(path.read_bytes())
    assert looks_like_simple_schema(rows), f"{filename} is not in the compact layout"
    adapted, report = adapt(rows, phase="PHASE_2", domain_code="CS")
    assert report.usable == expected_usable, report.notes()
    assert report.withheld == expected_withheld, report.notes()


def test_tamil_script_survives_the_round_trip():
    path = SEED / "Ph.D-ENTRANCE-EXAM-TAMILTAMILQuestion-_1_.csv"
    if not path.exists():
        pytest.skip("Tamil paper not present")
    rows = read_tabular(path.read_bytes())
    blob = " ".join(r["question_text"] for r in rows)
    tamil = sum(1 for ch in blob if "஀" <= ch <= "௿")
    assert tamil > 500, "Tamil script did not survive decoding"
    assert "?" not in rows[0]["question_text"][:-1], "text degraded to question marks"


def test_maths_symbols_survive_the_round_trip():
    path = SEED / "MATHS.csv"
    if not path.exists():
        pytest.skip("Maths paper not present")
    rows = read_tabular(path.read_bytes())
    blob = " ".join(r["question_text"] + " ".join(r[f"option_{k}"] for k in "abcd")
                    for r in rows)
    assert any(ch in blob for ch in "⊆λ∈≠α→∞"), "mathematical notation was lost"
