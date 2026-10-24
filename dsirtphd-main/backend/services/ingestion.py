import csv
import io
import re
import os
import tempfile
import subprocess
from typing import List, Dict, Any
import docx
from fastapi import UploadFile

from backend.models import Question, QuestionType

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

_OPTION_SPLIT_RE = re.compile(r'(?:^|[\s\n])([A-Da-dஅஆஇஈ])[\.\)]\s*')


def _split_option_line(text: str, matches=None) -> List[str]:
    """
    A single paragraph in these source .docx files can contain ONE option
    ("A. Rice"), all four crammed onto one line ("A. 25%  B. 30%  C. 40%
    D. 50%"), joined by an embedded manual line break, OR (some domains,
    e.g. Entomology) three lettered options with the FIRST option having
    no letter at all ("Spring tail   b. Dragonfly   c. Bed bugs   d.
    Silverfish").

    The old code only ever matched the first marker and kept the entire
    remainder of the paragraph as ONE option string — that's the root
    cause of the exam UI showing a single merged option instead of four.

    This splits on every A/B/C/D (or Tamil அ/ஆ/இ/ஈ) marker found in the
    paragraph. Any text BEFORE the first marker is kept as the leading
    option (covers the unlabeled-first-option case above); when the
    paragraph starts exactly at a marker, that leading text is empty and
    is dropped as before.
    """
    if matches is None:
        matches = list(_OPTION_SPLIT_RE.finditer(text))
    if not matches:
        return [text.strip()]
    options = []
    lead = text[:matches[0].start()].strip()
    if lead:
        options.append(lead)
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        val = text[start:end].strip()
        if val:
            options.append(val)
    return options if options else [text.strip()]


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
            
        # A paragraph containing 2+ option markers anywhere in it is an
        # option cluster (covers merged-onto-one-line and unlabeled-first-
        # option formats) — check this before the single-marker-at-start
        # case below so those messier formats get split correctly.
        inline_matches = list(_OPTION_SPLIT_RE.finditer(text))
        if len(inline_matches) >= 2:
            if current_q is None:
                current_q = {
                    'parsed_no': len(questions) + 1,
                    'domain': domain, 'phase': phase,
                    'question_type': QuestionType.mcq,
                    'question_text': "Question text missing",
                    'options': []
                }
            current_q['options'].extend(_split_option_line(text, inline_matches))
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
            current_q['options'].extend(_split_option_line(text))
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

def parse_csv_questions(file_bytes: bytes, domain: str, phase: int) -> List[Question]:
    text = file_bytes.decode('utf-8')
    reader = csv.DictReader(io.StringIO(text))
    questions_data = []
    
    for row in reader:
        options = [row.get('Option A', ''), row.get('Option B', ''), row.get('Option C', ''), row.get('Option D', '')]
        correct_map = {'A': 0, 'B': 1, 'C': 2, 'D': 3}
        c_val = row.get('Correct Option', 'A').strip().upper()
        
        q = Question(
            order_no=int(row.get('S.No', len(questions_data)+1)),
            domain=domain,
            phase=phase,
            question_type=QuestionType.mcq,
            question_text=row.get('Question', ''),
            options=options,
            correct_option=correct_map.get(c_val, 0)
        )
        questions_data.append(q)
    return questions_data
