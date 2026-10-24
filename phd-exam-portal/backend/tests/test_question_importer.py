"""Tests for the strict two-pass importer's validation pass.

Pass 1 touches no database, so everything here runs offline.
"""
from __future__ import annotations

import csv
import io

import pytest

from app.enums import Difficulty, Phase, QuestionType
from app.services.question_importer import (
    REQUIRED_COLUMNS,
    build_template_csv,
    parse_rows,
    read_tabular,
)
from app.services.text_utils import normalise_text, question_md5

DOMAINS = {"CS", "MATH", "ENG"}


def row(**overrides) -> dict[str, str]:
    base = {
        "phase": "PHASE_2",
        "domain_code": "CS",
        "question_type": "SINGLE_CHOICE",
        "question_text": "What is 2 + 2?",
        "option_a": "3",
        "option_b": "4",
        "option_c": "5",
        "option_d": "6",
        "correct_answer": "B",
        "marks": "1",
        "negative_marks": "0",
    }
    base.update(overrides)
    return base


# ------------------------------------------------------------- happy paths
def test_valid_row_parses():
    parsed, report = parse_rows([row()], DOMAINS)
    assert report.ok, report.errors
    assert len(parsed) == 1
    q = parsed[0]
    assert q.phase is Phase.PHASE_2
    assert q.question_type is QuestionType.SINGLE_CHOICE
    assert q.difficulty is Difficulty.MEDIUM
    assert [(lbl, correct) for lbl, _, correct in q.options] == [
        ("A", False), ("B", True), ("C", False), ("D", False)
    ]


@pytest.mark.parametrize("answer", ["B", "b", " b ", "2"])
def test_correct_answer_accepts_label_and_index(answer):
    parsed, report = parse_rows([row(correct_answer=answer)], DOMAINS)
    assert report.ok, report.errors
    assert [lbl for lbl, _, c in parsed[0].options if c] == ["B"]


def test_correct_answer_accepts_exact_option_text():
    parsed, report = parse_rows(
        [row(option_a="Paris", option_b="Rome", option_c="Bonn", option_d="Oslo",
             correct_answer="Rome")],
        DOMAINS,
    )
    assert report.ok, report.errors
    assert [lbl for lbl, _, c in parsed[0].options if c] == ["B"]


def test_ambiguous_numeric_answer_is_rejected_not_guessed():
    """Options 3/4/5/6 with correct_answer "4": the text says B, the position
    says D. Guessing either would silently mis-key the question."""
    _, report = parse_rows([row(correct_answer="4")], DOMAINS)
    assert not report.ok
    assert "ambiguous" in report.errors[0].message


def test_multiple_choice_accepts_several_answers():
    parsed, report = parse_rows(
        [row(question_type="MULTIPLE_CHOICE", correct_answer="B,D")], DOMAINS
    )
    assert report.ok, report.errors
    assert sorted(lbl for lbl, _, c in parsed[0].options if c) == ["B", "D"]


def test_phase1_row_without_domain_is_valid():
    parsed, report = parse_rows([row(phase="PHASE_1", domain_code="")], DOMAINS)
    assert report.ok, report.errors
    assert parsed[0].domain_code is None


# --------------------------------------------------------------- rejections
def test_missing_required_column_is_reported_once():
    bad = row()
    del bad["correct_answer"]
    parsed, report = parse_rows([bad], DOMAINS)
    assert not report.ok
    assert "correct_answer" in report.errors[0].message
    assert parsed == []


def test_unknown_domain_code_is_rejected_with_row_number():
    _, report = parse_rows([row(domain_code="NOPE")], DOMAINS)
    assert not report.ok
    err = report.errors[0]
    assert err.row == 2          # header is row 1
    assert err.column == "domain_code"
    assert "NOPE" in err.message


def test_phase1_with_domain_code_is_rejected():
    _, report = parse_rows([row(phase="PHASE_1", domain_code="CS")], DOMAINS)
    assert not report.ok
    assert "must not carry a domain_code" in report.errors[0].message


def test_phase2_without_domain_code_is_rejected():
    _, report = parse_rows([row(domain_code="")], DOMAINS)
    assert not report.ok
    assert "require a domain_code" in report.errors[0].message


def test_correct_answer_matching_nothing_is_rejected():
    _, report = parse_rows([row(correct_answer="Z")], DOMAINS)
    assert not report.ok
    assert report.errors[0].column == "correct_answer"


def test_single_choice_with_two_answers_is_rejected():
    _, report = parse_rows([row(correct_answer="A,B")], DOMAINS)
    assert not report.ok
    assert "exactly one correct answer" in report.errors[0].message


def test_empty_question_text_is_rejected():
    _, report = parse_rows([row(question_text="   ")], DOMAINS)
    assert not report.ok
    assert report.errors[0].column == "question_text"


def test_single_option_is_rejected():
    _, report = parse_rows(
        [row(option_b="", option_c="", option_d="", correct_answer="A")], DOMAINS
    )
    assert not report.ok
    assert "at least 2 options" in report.errors[0].message


def test_non_numeric_marks_is_rejected():
    _, report = parse_rows([row(marks="lots")], DOMAINS)
    assert not report.ok
    assert report.errors[0].column == "marks"


def test_negative_negative_marks_is_rejected():
    _, report = parse_rows([row(negative_marks="-1")], DOMAINS)
    assert not report.ok


def test_invalid_enum_lists_the_allowed_values():
    _, report = parse_rows([row(question_type="ESSAY")], DOMAINS)
    assert not report.ok
    assert "SINGLE_CHOICE" in report.errors[0].message


def test_one_bad_row_rejects_the_whole_file():
    parsed, report = parse_rows([row(), row(domain_code="NOPE"), row()], DOMAINS)
    assert not report.ok
    assert parsed == []          # nothing is handed to the write pass
    assert report.errors[0].row == 3


def test_every_bad_row_is_reported_not_just_the_first():
    _, report = parse_rows(
        [row(domain_code="NOPE"), row(marks="x"), row(correct_answer="Z")], DOMAINS
    )
    assert len({e.row for e in report.errors}) == 3


# --------------------------------------------------------------- duplicates
def test_duplicate_inside_the_file_is_skipped_with_a_warning():
    parsed, report = parse_rows([row(), row()], DOMAINS)
    assert report.ok
    assert len(parsed) == 1
    assert report.duplicates_in_file == 1
    assert "duplicate" in report.warnings[0]


def test_duplicate_detection_ignores_whitespace_and_case():
    a = row(question_text="What is 2 + 2?")
    b = row(question_text="  what   is 2 + 2?  ")
    parsed, report = parse_rows([a, b], DOMAINS)
    assert len(parsed) == 1
    assert report.duplicates_in_file == 1


def test_hash_is_stable_across_nbsp_and_normal_space():
    assert question_md5("a b") == question_md5("a b")


def test_normalise_strips_bom_and_zero_width():
    assert normalise_text("﻿hello​ world") == "hello world"


# ------------------------------------------------------------------ reading
def test_utf8_bom_csv_headers_are_clean():
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(REQUIRED_COLUMNS))
    writer.writeheader()
    writer.writerow(row())
    data = b"\xef\xbb\xbf" + buf.getvalue().encode("utf-8")
    rows = read_tabular(data)
    assert "phase" in rows[0]
    assert "﻿phase" not in rows[0]
    _, report = parse_rows(rows, DOMAINS)
    assert report.ok, report.errors


def test_regional_script_and_maths_symbols_survive():
    text = "தமிழ் இலக்கணம் ∫x²dx என்பது யாது?"
    rows = [row(question_text=text)]
    parsed, report = parse_rows(rows, DOMAINS)
    assert report.ok, report.errors
    assert parsed[0].question_text == text


def test_shipped_template_validates_against_its_own_parser():
    rows = read_tabular(build_template_csv().encode("utf-8"))
    _, report = parse_rows(rows, {"CS"})
    assert report.ok, report.errors
