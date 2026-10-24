"""
Question bank management — available to admins and question setters alike.
Candidates never hit this router; their question-facing endpoints
(backend/routers/exam.py) only ever return the shuffled, answer-stripped
view built by exam_engine.get_question_for_display.
"""
import csv
import io
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from backend import models, schemas
from backend.config import DOMAINS
from backend.database import get_db
from backend.security import get_current_staff

router = APIRouter(prefix="/questions", tags=["questions"])

CSV_COLUMNS = ["domain", "question_type", "question_text", "option_a", "option_b", "option_c", "option_d", "correct_option"]


def _validate_payload(payload: schemas.QuestionIn):
    if payload.question_type == "mcq":
        if not payload.options or len(payload.options) < 2:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="MCQ questions need at least 2 options")
        if payload.correct_option is None or not (0 <= payload.correct_option < len(payload.options)):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="correct_option must index into options")
    else:  # written
        if payload.options or payload.correct_option is not None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Written questions must not have options or correct_option")


def _to_out(q: models.Question) -> schemas.QuestionAdminOut:
    return schemas.QuestionAdminOut(
        id=q.id,
        order_no=q.order_no,
        domain=q.domain,
        question_type=q.question_type.value,
        question_text=q.question_text,
        options=q.options,
        correct_option=q.correct_option,
    )


@router.get("/meta", response_model=schemas.QuestionBankMeta)
def get_meta(staff: models.User = Depends(get_current_staff)):
    """Single source of truth for the domain dropdown/filter in the UI —
    the frontend never hardcodes the domain list."""
    return schemas.QuestionBankMeta(domains=DOMAINS)


@router.get("/bulk-import/template")
def download_csv_template(staff: models.User = Depends(get_current_staff)):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(CSV_COLUMNS)
    writer.writerow([DOMAINS[0], "mcq", "Example MCQ question text?", "Option A", "Option B", "Option C", "Option D", "A"])
    writer.writerow([DOMAINS[0], "written", "Example written/subjective question text?", "", "", "", "", ""])
    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=question_bank_template.csv"},
    )


@router.get("", response_model=List[schemas.QuestionAdminOut])
def list_questions(
    domain: Optional[str] = None,
    question_type: Optional[str] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    staff: models.User = Depends(get_current_staff),
):
    query = db.query(models.Question)
    if domain:
        query = query.filter(models.Question.domain == domain)
    if question_type:
        query = query.filter(models.Question.question_type == models.QuestionType(question_type))
    if search:
        query = query.filter(models.Question.question_text.ilike(f"%{search}%"))
    questions = query.order_by(models.Question.domain, models.Question.order_no).all()
    return [_to_out(q) for q in questions]


@router.post("", response_model=schemas.QuestionAdminOut, status_code=status.HTTP_201_CREATED)
def create_question(
    payload: schemas.QuestionIn,
    db: Session = Depends(get_db),
    staff: models.User = Depends(get_current_staff),
):
    _validate_payload(payload)
    q = models.Question(
        order_no=payload.order_no,
        domain=payload.domain,
        question_type=models.QuestionType(payload.question_type),
        question_text=payload.question_text,
        options=payload.options,
        correct_option=payload.correct_option,
    )
    db.add(q)
    db.commit()
    db.refresh(q)
    return _to_out(q)


@router.put("/{question_id}", response_model=schemas.QuestionAdminOut)
def update_question(
    question_id: int,
    payload: schemas.QuestionIn,
    db: Session = Depends(get_db),
    staff: models.User = Depends(get_current_staff),
):
    _validate_payload(payload)
    q = db.query(models.Question).filter(models.Question.id == question_id).first()
    if not q:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found")

    q.order_no = payload.order_no
    q.domain = payload.domain
    q.question_type = models.QuestionType(payload.question_type)
    q.question_text = payload.question_text
    q.options = payload.options
    q.correct_option = payload.correct_option
    db.add(q)
    db.commit()
    db.refresh(q)
    return _to_out(q)


@router.delete("/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_question(
    question_id: int,
    db: Session = Depends(get_db),
    staff: models.User = Depends(get_current_staff),
):
    q = db.query(models.Question).filter(models.Question.id == question_id).first()
    if not q:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Question not found")
    # Answers reference questions by id but aren't FK-cascaded from this
    # side; block deletion if any exam session has already used it so we
    # never orphan/corrupt a candidate's in-progress or historical answers.
    in_use = db.query(models.Answer).filter(models.Answer.question_id == question_id).first()
    if in_use:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This question is referenced by an exam session and can't be deleted.",
        )
    db.delete(q)
    db.commit()


def _parse_correct_option(raw: str, n_options: int, row_num: int) -> int:
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("correct_option is required for mcq rows")
    # Accept "A"/"B"/"C"/"D" (case-insensitive) or a 1-based number, since
    # that's what someone filling this in from a spreadsheet will type.
    if raw.upper() in ("A", "B", "C", "D"):
        idx = ord(raw.upper()) - ord("A")
    else:
        try:
            idx = int(raw) - 1
        except ValueError:
            raise ValueError(f'correct_option "{raw}" must be A/B/C/D or a number (1-based)')
    if not (0 <= idx < n_options):
        raise ValueError(f"correct_option {raw} is out of range for {n_options} options given")
    return idx


@router.post("/bulk-import", response_model=schemas.BulkImportResult)
async def bulk_import(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    staff: models.User = Depends(get_current_staff),
):
    """
    CSV columns: domain, question_type, question_text, option_a, option_b,
    option_c, option_d, correct_option. Leave the option columns blank for
    written questions. Download the template from GET /questions/bulk-import/template
    to get the exact header row and an example of each row type.

    Every valid row is committed; invalid rows are skipped and reported by
    row number so a bad row 214 out of 500 doesn't block the other 499.
    """
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")  # -sig eats the BOM Excel adds on export
    except UnicodeDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File isn't valid UTF-8 text/CSV")

    reader = csv.DictReader(io.StringIO(text))
    missing_cols = [c for c in ("domain", "question_type", "question_text") if c not in (reader.fieldnames or [])]
    if missing_cols:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"CSV is missing required column(s): {', '.join(missing_cols)}. "
                   f"Download the template from /questions/bulk-import/template.",
        )

    next_order_no = (db.query(models.Question).count()) + 1
    created = 0
    errors: List[schemas.BulkImportRowError] = []
    to_add = []

    for row_num, row in enumerate(reader, start=2):  # row 1 is the header
        domain = (row.get("domain") or "").strip()
        qtype = (row.get("question_type") or "").strip().lower()
        text_ = (row.get("question_text") or "").strip()

        if not domain and not qtype and not text_:
            continue  # silently skip fully blank rows (trailing spreadsheet rows)

        try:
            if domain not in DOMAINS:
                raise ValueError(f'domain "{domain}" is not one of the configured DOMAINS in config.py')
            if qtype not in ("mcq", "written"):
                raise ValueError('question_type must be "mcq" or "written"')
            if not text_:
                raise ValueError("question_text is required")

            options = None
            correct_option = None
            if qtype == "mcq":
                options = [row.get(c, "").strip() for c in ("option_a", "option_b", "option_c", "option_d")]
                options = [o for o in options if o]
                if len(options) < 2:
                    raise ValueError("mcq rows need at least 2 non-empty options")
                correct_option = _parse_correct_option(row.get("correct_option", ""), len(options), row_num)

            to_add.append(models.Question(
                order_no=next_order_no,
                domain=domain,
                question_type=models.QuestionType(qtype),
                question_text=text_,
                options=options,
                correct_option=correct_option,
            ))
            next_order_no += 1
            created += 1
        except ValueError as e:
            errors.append(schemas.BulkImportRowError(row=row_num, message=str(e)))

    if to_add:
        db.add_all(to_add)
        db.commit()

    return schemas.BulkImportResult(created=created, errors=errors)
