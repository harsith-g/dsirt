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
from backend.services.ingestion import parse_csv_rows

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


@router.post("/bulk-import", response_model=schemas.BulkImportResult)
async def bulk_import(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    staff: models.User = Depends(get_current_staff),
):
    """
    CSV columns: domain, question_type, question_text, option_a, option_b,
    option_c, option_d, correct_option (or correct_answer). Leave the
    option columns blank for written questions. Download the template from
    GET /questions/bulk-import/template for the exact header row and an
    example of each row type.

    This is the single-CSV-with-its-own-domain-column path, shared with
    the admin ZIP/CSV importer at POST /admin/questions/upload (see
    backend/services/ingestion.py) — same column parsing, same
    letter-or-number correct-answer handling, same partial-success
    behaviour: every valid row is committed, invalid rows are skipped and
    reported by row number so a bad row 214 out of 500 doesn't block the
    other 499.
    """
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")  # -sig eats the BOM Excel adds on export
    except UnicodeDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File isn't valid UTF-8 text/CSV")

    next_order_no = (db.query(models.Question).count()) + 1
    try:
        questions, row_errors = parse_csv_rows(
            text, default_domain=None, default_phase=None,
            start_order_no=next_order_no, domains_allowlist=DOMAINS,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{e} Download the template from /questions/bulk-import/template.",
        )

    if questions:
        db.add_all(questions)
        db.commit()

    errors = [schemas.BulkImportRowError(row=e.row, message=e.message) for e in row_errors]
    return schemas.BulkImportResult(created=len(questions), errors=errors)
