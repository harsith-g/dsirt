"""Tests for the legacy question-paper parser.

These run without a database or any network. They cover the four answer-key
layouts, the section-heading/option ambiguity that the sequence rule resolves,
and the guarantee that a question is never given an answer the source did not
supply.
"""
from __future__ import annotations

import pathlib

import pytest

from app.services.raw_paper_parser import (
    extract_answer_key,
    parse_paper,
    to_import_rows,
    to_lines,
)

# The originals, in the raw Word/Excel-export shape. seed_data now holds the
# corrected compact-schema files, so the tier-3 fallback parser is exercised
# against these preserved fixtures instead.
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


def _csv(text: str) -> bytes:
    return "\n".join('"' + line.replace('"', '""') + '"' for line in text.split("\n")).encode()


# ------------------------------------------------------------ option shapes
def test_options_one_per_line():
    paper = _csv(
        "1. What is the capital of France?\n"
        "A. Berlin\n"
        "B. Paris\n"
        "C. Madrid\n"
        "D. Rome\n"
        "Answer Key\n"
        "1\nB"
    )
    questions, report = parse_paper(paper, "t.csv")
    assert report.accepted == 1
    assert questions[0].correct_label == "B"
    assert [t for _, t in questions[0].options] == ["Berlin", "Paris", "Madrid", "Rome"]


def test_options_sharing_one_line():
    paper = _csv(
        "1. Which of the following is a Rabi crop?\n"
        "A. Rice   B. Wheat   C. Cotton   D. Maize\n"
        "Answer Key\n1\nB"
    )
    questions, report = parse_paper(paper, "t.csv")
    assert report.accepted == 1
    assert [t for _, t in questions[0].options] == ["Rice", "Wheat", "Cotton", "Maize"]


def test_options_split_across_lines():
    paper = _csv(
        "1. Principles of conservation agriculture?\n"
        "A. Intensive tillage   B. Minimum disturbance\n"
        "C. Deep ploughing   D. Flooding\n"
        "Answer Key\n1\nB"
    )
    questions, _ = parse_paper(paper, "t.csv")
    assert len(questions[0].options) == 4


def test_label_without_space_is_still_an_option():
    """Entomology Q19 has "c.5,6 &10" with no space after the period."""
    paper = _csv(
        "1. Prolegs are present in which segments?\n"
        "a. 3,4,6 &10     b. 3,4,5,6 &10     c.5,6 &10     d. 6 &10\n"
        "Answer Key\n1\nD"
    )
    questions, report = parse_paper(paper, "t.csv")
    assert report.accepted == 1
    assert len(questions[0].options) == 4
    assert questions[0].correct_label == "D"


def test_abbreviation_in_prose_is_not_an_option_boundary():
    """A stray "B." inside option A's text must not split the option."""
    paper = _csv(
        "1. Which degree is meant?\n"
        "A. A B. Tech qualification from any university\n"
        "B. A doctorate\n"
        "Answer Key\n1\nB"
    )
    questions, _ = parse_paper(paper, "t.csv")
    labels = [lbl for lbl, _ in questions[0].options]
    assert labels == ["A", "B"]


def test_section_heading_is_not_mistaken_for_an_option():
    """"B. Computer Graphics" is a heading; "B. Congestion control" is option B."""
    paper = _csv(
        "A. Computer Networks\n"
        "1. Which mechanism controls this?\n"
        "A. Flow control using the receiver window\n"
        "B. Congestion control using the congestion window\n"
        "B. Computer Graphics\n"
        "2. What causes a vanishing point?\n"
        "A. Clipping\n"
        "B. Perspective projection\n"
        "Answer Key\n1\nB\n2\nB"
    )
    questions, report = parse_paper(paper, "t.csv")
    assert report.accepted == 2
    first = {lbl: txt for lbl, txt in questions[0].options}
    assert first["B"] == "Congestion control using the congestion window"
    assert "Computer Graphics" not in " ".join(first.values())


# --------------------------------------------------------- answer-key layouts
def test_key_layout_vertical_blob():
    lines = to_lines(_csv("Answer Key\nQ.No.\nAnswer\n1\nC\n2\nB\n3\nA"))
    key, conflicts, _ = extract_answer_key(lines)
    assert key == {1: "C", 2: "B", 3: "A"}
    assert conflicts == []


def test_key_layout_inline_runs():
    body = "Answer Key\n" + "  ".join(f"{i}. B" for i in range(1, 26))
    key, _, _ = extract_answer_key(to_lines(_csv(body)))
    assert len(key) == 25
    assert key[7] == "B"


def test_key_layout_without_heading_is_still_found():
    """The English paper runs straight into a Q/Ans grid with no heading."""
    tokens = ["Q", "Ans"] * 3
    for i in range(1, 41):
        tokens += [str(i), "A" if i % 2 else "B"]
    paper = _csv("1. A question?\nA. one\nB. two\n" + "\n".join(tokens))
    key, _, idx = extract_answer_key(to_lines(paper))
    assert len(key) == 40
    assert idx < len(to_lines(paper))


def test_key_conflicts_are_reported_not_silently_resolved():
    lines = to_lines(_csv("Answer Key\n1\nA\n1\nB"))
    key, conflicts, _ = extract_answer_key(lines)
    assert conflicts
    assert key[1] == "A"  # first wins, conflict surfaced


# --------------------------------------------------- withholding, not guessing
def test_question_without_key_entry_is_withheld():
    paper = _csv("1. Q one?\nA. a\nB. b\n2. Q two?\nA. a\nB. b\nAnswer Key\n1\nA")
    questions, report = parse_paper(paper, "t.csv")
    assert report.accepted == 1
    assert report.rejected_no_key == [2]
    assert all(q.correct_label is not None for q in questions)


def test_key_pointing_at_a_missing_option_is_withheld():
    paper = _csv("1. Q?\nA. a\nB. b\nAnswer Key\n1\nD")
    questions, report = parse_paper(paper, "t.csv")
    assert questions == []
    assert report.rejected_bad_key == [1]


def test_question_with_fewer_than_two_options_is_withheld():
    paper = _csv("1. Q?\nA. only one\nAnswer Key\n1\nA")
    questions, report = parse_paper(paper, "t.csv")
    assert questions == []
    assert report.rejected_few_options == [1]


def test_paper_with_no_key_at_all_yields_nothing():
    paper = _csv("1. Q?\nA. a\nB. b\nC. c\nD. d")
    questions, report = parse_paper(paper, "t.csv")
    assert questions == []
    assert report.parsed == 1
    assert report.key_entries == 0
    assert report.notes


# --------------------------------------------------------------- integration
@pytest.mark.parametrize(
    "filename,min_accepted",
    [
        ("raw_research_methodology.csv", 50),
        ("raw_entomology.csv", 50),
        ("raw_english_xlsx_named_csv.csv", 50),
        ("raw_computer_science.csv", 45),
        ("raw_agronomy.csv", 35),
    ],
)
def test_raw_papers_parse(filename, min_accepted):
    path = FIXTURES / filename
    if not path.exists():
        pytest.skip(f"{filename} not present")
    questions, report = parse_paper(path.read_bytes(), filename)
    assert report.accepted >= min_accepted, report.summary()
    for q in questions:
        assert q.text
        assert len(q.options) >= 2
        assert q.correct_label in [lbl for lbl, _ in q.options]


def test_xlsx_named_csv_is_detected_by_magic_bytes():
    """The English paper originally arrived as an XLSX named .csv."""
    path = FIXTURES / "raw_english_xlsx_named_csv.csv"
    if not path.exists():
        pytest.skip("fixture not present")
    assert path.read_bytes()[:4] == b"PK\x03\x04"
    _, report = parse_paper(path.read_bytes(), "english.csv")
    assert report.accepted > 0


def test_to_import_rows_shape():
    paper = _csv("1. Q?\nA. a\nB. b\nAnswer Key\n1\nB")
    questions, _ = parse_paper(paper, "t.csv")
    rows = to_import_rows(questions, phase="PHASE_2", domain_code="CS", source_prefix="CS-")
    assert rows[0]["phase"] == "PHASE_2"
    assert rows[0]["domain_code"] == "CS"
    assert rows[0]["correct_answer"] == "B"
    assert rows[0]["option_a"] == "a"
    assert rows[0]["source_ref"] == "CS-Q1"
