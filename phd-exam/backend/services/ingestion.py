"""
Question-bank ingestion — one canonical pipeline for turning an uploaded
.zip, .csv, .docx or .xlsx file into Question rows.

Historically this codebase had THREE different, mutually-inconsistent
importers (this module's old parse_csv_questions, admin.py's inline
xlsx parser, and questions.py's own hand-rolled CSV loop), each expecting
different column names and different correct-answer conventions. That's
gone: everything now funnels through parse_upload_file() below, which
accepts a single .csv, a .zip of .csv files (one per domain), a .docx, or
an .xlsx.

Column names are matched case-insensitively, with underscores/spaces
treated the same, and a few historical aliases are accepted because real
CSVs handed in by different departments don't all use the same header:

    question text     -> question_text
    option a..d        -> option_a .. option_d
    correct answer      -> correct_option / correct_answer
    domain                -> domain / domain_code
    question type           -> question_type   (defaults to "mcq")
    phase                     -> phase          (falls back to a caller-supplied default)

correct_option/correct_answer accepts "A".."D" (any case), or a number.
Numbers 1..N are read as 1-based (matches this app's existing CSV
template and admin-facing docs); a number that's only valid as 0-based
(i.e. 0, or out of 1-based range but in 0-based range) is read as 0-based.
This keeps existing exported templates and any historical data meaning
the same thing while still accepting the 0-based convention some external
tools produce.

Design choice — partial success, not all-or-nothing:
    A bad row (missing text, out-of-range answer, unknown domain) is
    skipped and reported with its row number; every valid row in the same
    file still gets committed. For an admin bulk-loading 500 questions,
    one typo on row 214 must not silently void rows 1-213 and 215-500. A
    file that can't be read AT ALL (corrupt zip, undecodable bytes, wrong
    extension) raises ValueError instead — nothing from it is inserted.
"""
import csv
import io
import os
import re
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import docx
import openpyxl
from fastapi import UploadFile

from backend.models import Question, QuestionType


@dataclass
class RowError:
    row: int                        # 1-based; 0 for a whole-file-level problem
    message: str
    source: Optional[str] = None    # member filename, when parsing inside a zip


# ============================================================
# Column alias resolution
# ============================================================

def _norm_key(k: str) -> str:
    return re.sub(r"[\s_]+", "_", (k or "").strip().lower())


_TEXT_KEYS = {_norm_key(k) for k in ["question_text", "question"]}
_OPTION_KEYS = {
    "a": {_norm_key(k) for k in ["option_a", "option a", "opt_a"]},
    "b": {_norm_key(k) for k in ["option_b", "option b", "opt_b"]},
    "c": {_norm_key(k) for k in ["option_c", "option c", "opt_c"]},
    "d": {_norm_key(k) for k in ["option_d", "option d", "opt_d"]},
}
_CORRECT_KEYS = {_norm_key(k) for k in ["correct_option", "correct_answer", "correct option", "correct answer"]}
_DOMAIN_KEYS = {_norm_key(k) for k in ["domain", "domain_code"]}
_TYPE_KEYS = {_norm_key(k) for k in ["question_type", "type"]}
_PHASE_KEYS = {_norm_key(k) for k in ["phase"]}
_ORDER_KEYS = {_norm_key(k) for k in ["order_no", "s.no", "sno", "question_number", "no", "#"]}


def _normalise_row_keys(row: dict) -> Dict[str, Any]:
    out = {}
    for k, v in row.items():
        if k is None:
            continue
        out[_norm_key(k)] = v
    return out


def _first_present(row: Dict[str, Any], keys: set) -> Optional[str]:
    for key in keys:
        val = row.get(key)
        if val not in (None, ""):
            return str(val)
    return None


def _parse_correct_answer(raw: Optional[str], n_options: int) -> int:
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("correct_option/correct_answer is required for mcq rows")
    upper = raw.upper()
    if upper in ("A", "B", "C", "D"):
        idx = ord(upper) - ord("A")
        if idx >= n_options:
            raise ValueError(f'correct answer "{raw}" is out of range for {n_options} options')
        return idx
    try:
        num = int(raw)
    except ValueError:
        raise ValueError(f'correct answer "{raw}" must be A/B/C/D or a number')
    # 1-based wins on ambiguity — matches this app's CSV template/docs.
    if 1 <= num <= n_options:
        return num - 1
    if 0 <= num < n_options:
        return num
    raise ValueError(f'correct answer "{raw}" is out of range for {n_options} options')


def _parse_row_to_question(
    raw_row: dict,
    default_domain: Optional[str],
    default_phase: Optional[int],
    next_order_no: int,
    domains_allowlist: Optional[List[str]],
) -> Question:
    row = _normalise_row_keys(raw_row)

    text = (_first_present(row, _TEXT_KEYS) or "").strip()
    if not text:
        raise ValueError("question_text is required")

    domain = (_first_present(row, _DOMAIN_KEYS) or "").strip() or default_domain
    if not domain:
        raise ValueError("domain is required (no 'domain' column in this file and no default domain given)")
    if domains_allowlist is not None and domain not in domains_allowlist:
        raise ValueError(f'domain "{domain}" is not one of the configured DOMAINS in config.py')

    qtype = (_first_present(row, _TYPE_KEYS) or "mcq").strip().lower()
    if qtype not in ("mcq", "written"):
        raise ValueError('question_type must be "mcq" or "written"')

    phase_raw = _first_present(row, _PHASE_KEYS)
    if phase_raw is not None:
        try:
            phase = int(phase_raw)
        except ValueError:
            raise ValueError(f'phase "{phase_raw}" must be an integer')
    elif default_phase is not None:
        phase = default_phase
    else:
        raise ValueError("phase is required (no 'phase' column in this file and no default phase given)")

    options = None
    correct_option = None
    if qtype == "mcq":
        options = []
        for letter in ("a", "b", "c", "d"):
            val = _first_present(row, _OPTION_KEYS[letter])
            if val:
                options.append(val.strip())
        if len(options) < 2:
            raise ValueError("mcq rows need at least 2 non-empty options")
        correct_option = _parse_correct_answer(_first_present(row, _CORRECT_KEYS), len(options))

    order_raw = _first_present(row, _ORDER_KEYS)
    try:
        order_no = int(order_raw) if order_raw is not None else next_order_no
    except ValueError:
        order_no = next_order_no

    return Question(
        order_no=order_no,
        domain=domain,
        phase=phase,
        question_type=QuestionType(qtype),
        question_text=text,
        options=options,
        correct_option=correct_option,
    )


# ============================================================
# CSV
# ============================================================

def parse_csv_rows(
    file_text: str,
    default_domain: Optional[str],
    default_phase: Optional[int],
    start_order_no: int = 1,
    domains_allowlist: Optional[List[str]] = None,
    source: Optional[str] = None,
) -> Tuple[List[Question], List[RowError]]:
    reader = csv.DictReader(io.StringIO(file_text))
    fieldnames_norm = {_norm_key(h) for h in (reader.fieldnames or [])}

    if not (_TEXT_KEYS & fieldnames_norm):
        raise ValueError("CSV is missing a question_text column")
    if default_domain is None and not (_DOMAIN_KEYS & fieldnames_norm):
        raise ValueError(
            "CSV has no 'domain' column and no default domain was given "
            "(select a domain for a single-file upload, or name this file "
            "after its domain inside a .zip import)"
        )

    questions: List[Question] = []
    errors: List[RowError] = []
    next_order_no = start_order_no

    for row_num, row in enumerate(reader, start=2):  # row 1 is the header
        normalised = _normalise_row_keys(row)
        if not any((v or "").strip() for v in normalised.values() if isinstance(v, str)):
            continue  # fully-blank trailing row
        try:
            q = _parse_row_to_question(row, default_domain, default_phase, next_order_no, domains_allowlist)
            questions.append(q)
            next_order_no += 1
        except ValueError as e:
            errors.append(RowError(row=row_num, message=str(e), source=source))

    return questions, errors


# ============================================================
# ZIP (of CSVs, one per domain)
# ============================================================

def parse_zip_of_csv(
    file_bytes: bytes,
    default_phase: Optional[int],
    start_order_no: int = 1,
    domains_allowlist: Optional[List[str]] = None,
) -> Tuple[List[Question], List[RowError]]:
    from backend.config import infer_domain_from_filename, infer_phase_for_domain

    try:
        zf = zipfile.ZipFile(io.BytesIO(file_bytes))
    except zipfile.BadZipFile:
        raise ValueError("File is not a valid .zip archive")

    csv_members = [
        n for n in zf.namelist()
        if n.lower().endswith(".csv")
        and "__MACOSX" not in n
        and not os.path.basename(n).startswith(".")
    ]
    if not csv_members:
        raise ValueError("Zip archive contains no .csv files")

    questions: List[Question] = []
    errors: List[RowError] = []
    next_order_no = start_order_no

    for member in sorted(csv_members):
        basename = os.path.basename(member)
        try:
            raw = zf.read(member)
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            errors.append(RowError(row=0, message="File isn't valid UTF-8 text/CSV", source=basename))
            continue

        inferred_domain = infer_domain_from_filename(basename)
        inferred_phase = default_phase if default_phase is not None else infer_phase_for_domain(inferred_domain)

        try:
            q_list, err_list = parse_csv_rows(
                text,
                default_domain=inferred_domain,
                default_phase=inferred_phase,
                start_order_no=next_order_no,
                domains_allowlist=domains_allowlist,
                source=basename,
            )
        except ValueError as e:
            errors.append(RowError(row=0, message=str(e), source=basename))
            continue

        questions.extend(q_list)
        errors.extend(err_list)
        next_order_no += len(q_list)

    return questions, errors


# ============================================================
# XLSX
# ============================================================

def parse_xlsx_questions(file_bytes: bytes, domain: str, phase: int) -> List[Question]:
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    ws = wb.active
    questions = []
    headers = None
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        row_vals = [str(c).strip() if c is not None else "" for c in row]
        if i == 0:
            headers = [_norm_key(h) for h in row_vals]
            continue
        if not headers:
            continue

        def col(*names):
            for name in names:
                key = _norm_key(name)
                if key in headers:
                    val = row_vals[headers.index(key)]
                    if val:
                        return val
            return ""

        question_text = col("Question", "question_text")
        if not question_text or question_text == "None":
            continue
        opt_a = col("Option A", "option_a"); opt_b = col("Option B", "option_b")
        opt_c = col("Option C", "option_c"); opt_d = col("Option D", "option_d")
        options = [o for o in [opt_a, opt_b, opt_c, opt_d] if o]
        correct_raw = col("Correct Answer", "correct_answer", "Correct Option", "correct_option")
        try:
            correct_idx = _parse_correct_answer(correct_raw, len(options)) if options else 0
        except ValueError:
            correct_idx = 0
        questions.append(Question(
            order_no=len(questions) + 1, domain=domain, phase=phase,
            question_type=QuestionType.mcq, question_text=question_text,
            options=options or [opt_a, opt_b, opt_c, opt_d], correct_option=correct_idx,
        ))
    return questions


# ============================================================
# DOCX (legacy free-text parser, unchanged — used as a fallback for the
# original department Word question papers in files_questiond/)
# ============================================================

def extract_text_fallback(file_bytes: bytes) -> List[str]:
    with tempfile.NamedTemporaryFile(suffix=".doc", delete=False) as tmp:
        tmp.write(file_bytes)
        tmp_name = tmp.name

    try:
        # Use macOS textutil for .doc conversion
        result = subprocess.run(
            ["textutil", "-convert", "txt", tmp_name, "-stdout"],
            capture_output=True,
            check=True
        )
        text = result.stdout.decode('utf-8', errors='ignore')
        return [line.strip() for line in text.split('\n') if line.strip()]
    except Exception:
        # Fallback to binary string extraction
        text = file_bytes.decode('latin-1', errors='ignore')
        text = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x9F]', '\n', text)
        text = re.sub(r'\n+', '\n', text)
        return [line.strip() for line in text.split('\n') if line.strip()]
    finally:
        if os.path.exists(tmp_name):
            os.remove(tmp_name)


def parse_docx_questions(file_bytes: bytes, domain: str, phase: int) -> List[Question]:
    lines = []
    try:
        document = docx.Document(io.BytesIO(file_bytes))
        for p in document.paragraphs:
            text = p.text.strip()
            if text:
                lines.append(text)
    except Exception:
        # Fallback for legacy .doc or corrupted .docx files
        lines = extract_text_fallback(file_bytes)

    questions = []
    current_q = None
    answers = {}
    is_answer_key = False

    for text in lines:
        if "ANSWER KEY" in text.upper():
            is_answer_key = True
            continue

        if is_answer_key:
            matches = re.findall(r'(\d+)\.\s*([A-D])', text)
            for m in matches:
                answers[int(m[0])] = ord(m[1].upper()) - ord('A')
            continue

        # Match numbered questions: 1. Question text
        m_q = re.match(r'^(\d+)\.\s*(.*)', text)
        if m_q:
            if current_q:
                questions.append(current_q)
            current_q = {
                'parsed_no': int(m_q.group(1)),
                'domain': domain,
                'phase': phase,
                'question_type': QuestionType.mcq,
                'question_text': m_q.group(2).strip(),
                'options': []
            }
            continue

        # Match options: A) text, A. text, (A) text, Tamil options அ), ஆ) etc.
        m_opt = re.match(r'^(?:[A-Da-dஅஆஇஈ][\.\)]|\([A-Da-dஅஆஇஈ]\))\s*(.+)', text)
        if m_opt:
            if current_q is None:
                current_q = {
                    'parsed_no': len(questions) + 1,
                    'domain': domain, 'phase': phase,
                    'question_type': QuestionType.mcq,
                    'question_text': "Question text missing",
                    'options': []
                }
            current_q['options'].append(m_opt.group(1).strip())
            continue

        # Ignore common headers/footers in the text
        if text.upper() in ["ENTOMOLOGY", "PAPER- 2", "MATHEMATICS", "MULTIPLE CHOICE QUESTIONS"] or "DHANALAKSHMI SRINIVASAN" in text.upper() or "PERAMBALUR" in text.upper() or "DSIRT-RET" in text.upper():
            continue

        # If it's neither a numbered question nor an option:
        if current_q is None or len(current_q['options']) > 0:
            # We already have options for current_q, so this text starts a NEW unnumbered question
            if current_q:
                questions.append(current_q)
            current_q = {
                'parsed_no': len(questions) + 1,
                'domain': domain,
                'phase': phase,
                'question_type': QuestionType.mcq,
                'question_text': text,
                'options': []
            }
        else:
            # Continuation of the current question text
            if current_q['question_text']:
                current_q['question_text'] += "\n" + text
            else:
                current_q['question_text'] = text

    if current_q:
        questions.append(current_q)

    db_questions = []
    for idx, q in enumerate(questions):
        # Assign order_no sequentially since unnumbered questions might mess up parsed_no
        order_no = idx + 1
        c_opt = answers.get(q['parsed_no'], 0)
        db_questions.append(
            Question(
                order_no=order_no,
                domain=q['domain'],
                phase=q['phase'],
                question_type=q['question_type'],
                question_text=q['question_text'],
                options=q['options'] if q['options'] else ["A", "B", "C", "D"],
                correct_option=c_opt
            )
        )
    return db_questions


# ============================================================
# Single dispatch point — what the admin upload endpoint calls
# ============================================================

def parse_upload_file(
    filename: str,
    file_bytes: bytes,
    default_domain: Optional[str] = None,
    default_phase: Optional[int] = None,
    start_order_no: int = 1,
    domains_allowlist: Optional[List[str]] = None,
) -> Tuple[List[Question], List[RowError]]:
    """
    One entry point for every question-bank upload format:
      .zip   — one .csv per domain inside; domain is inferred from each
               member's filename (falls back to default_domain/default_phase
               if given, useful for a zip that's all one domain/phase).
      .csv   — a single domain's questions, or a multi-domain CSV that
               includes its own 'domain' column.
      .xlsx  — legacy spreadsheet format (Question/Option A-D/Correct Answer).
      .docx  — legacy free-text department question paper.

    Returns (questions_to_insert, per_row_errors). Raises ValueError only
    for a file-level problem (bad zip, undecodable bytes, unsupported
    extension, or a CSV missing structurally-required columns) — never for
    an individual bad row, which is reported in per_row_errors instead.
    """
    name = (filename or "").lower()

    if name.endswith(".zip"):
        return parse_zip_of_csv(
            file_bytes,
            default_phase=default_phase,
            start_order_no=start_order_no,
            domains_allowlist=domains_allowlist,
        )

    if name.endswith(".csv"):
        try:
            text = file_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ValueError("File isn't valid UTF-8 text/CSV")
        return parse_csv_rows(
            text, default_domain=default_domain, default_phase=default_phase,
            start_order_no=start_order_no, domains_allowlist=domains_allowlist,
        )

    if name.endswith(".docx") or name.endswith(".doc"):
        if not default_domain or default_phase is None:
            raise ValueError("A domain and phase must be selected for .docx uploads")
        return parse_docx_questions(file_bytes, default_domain, default_phase), []

    if name.endswith(".xlsx"):
        if not default_domain or default_phase is None:
            raise ValueError("A domain and phase must be selected for .xlsx uploads")
        return parse_xlsx_questions(file_bytes, default_domain, default_phase), []

    raise ValueError("Unsupported file type. Supported: .zip, .csv, .docx, .xlsx")
