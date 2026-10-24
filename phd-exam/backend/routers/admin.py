import asyncio
import json
from datetime import datetime
from typing import List, Optional
import io
import csv

from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect, Query, UploadFile, File, Form, HTTPException, Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend import models, schemas, exam_engine
from backend.database import get_db, SessionLocal
from backend import security
from backend.config import MAX_STRIKES, CANDIDATE_DEFAULT_PASSWORD, DOMAINS
from backend.websocket_manager import manager
from backend.services.ingestion import parse_upload_file


router = APIRouter(tags=["admin"])


# ============================================================
# Global Exam Control
# ============================================================

@router.get("/admin/exam/state", response_model=schemas.GlobalExamStateOut)
def get_exam_state(
    db: Session = Depends(get_db),
    admin: models.User = Depends(security.get_current_admin),
):
    state = exam_engine.get_global_exam_state(db)
    return schemas.GlobalExamStateOut(
        exam_open=state.exam_open,
        opened_at=state.opened_at,
        closed_at=state.closed_at,
        scheduled_start_at=state.scheduled_start_at,
        scheduled_end_at=state.scheduled_end_at,
        timezone_label=state.timezone_label or "Asia/Kolkata",
    )


@router.post("/admin/exam/schedule")
def set_exam_schedule(
    payload: schemas.ExamScheduleRequest,
    db: Session = Depends(get_db),
    admin: models.User = Depends(security.get_current_admin),
):
    """Set the scheduled exam window (start and end times in UTC)."""
    if payload.scheduled_end_at <= payload.scheduled_start_at:
        raise HTTPException(status_code=400, detail="End time must be after start time.")
    state = exam_engine.set_exam_schedule(
        db,
        scheduled_start_at=payload.scheduled_start_at,
        scheduled_end_at=payload.scheduled_end_at,
        timezone_label=payload.timezone_label,
    )
    return {
        "status": "schedule_set",
        "scheduled_start_at": state.scheduled_start_at,
        "scheduled_end_at": state.scheduled_end_at,
        "timezone_label": state.timezone_label,
    }


@router.post("/admin/exam/start")
async def start_exam_globally(
    db: Session = Depends(get_db),
    admin: models.User = Depends(security.get_current_admin),
):
    state = exam_engine.set_global_exam_open(db, True)
    await manager.send_global_command({
        "type": "exam_opened",
        "message": "The examination is now open. You may begin.",
    })
    await manager.broadcast({
        "type": "exam_state_changed",
        "exam_open": True,
        "opened_at": state.opened_at.isoformat() if state.opened_at else None,
    })
    return {"status": "exam_opened", "opened_at": state.opened_at}


@router.post("/admin/exam/end")
async def end_exam_globally(
    db: Session = Depends(get_db),
    admin: models.User = Depends(security.get_current_admin),
):
    count = exam_engine.force_end_all_sessions(db)
    state = exam_engine.set_global_exam_open(db, False)
    await manager.send_global_command({
        "type": "exam_force_end",
        "message": "The examination has been ended by the administrator. Your responses have been submitted.",
    })
    await manager.broadcast({
        "type": "exam_state_changed",
        "exam_open": False,
        "closed_at": state.closed_at.isoformat() if state.closed_at else None,
        "sessions_force_ended": count,
    })
    return {"status": "exam_ended", "sessions_force_ended": count, "closed_at": state.closed_at}


# ============================================================
# Candidate monitoring
# ============================================================

@router.get("/admin/sessions", response_model=List[schemas.CandidateStatusOut])
def list_sessions(db: Session = Depends(get_db), admin: models.User = Depends(security.get_current_admin)):
    sessions = (
        db.query(models.ExamSession)
        .filter(models.ExamSession.status != models.SessionStatus.not_started)
        .all()
    )
    out = []
    for s in sessions:
        s = exam_engine.enforce_expiry(db, s)
        nav = exam_engine.build_nav(db, s)
        answered = sum(1 for n in nav if n["answered"])
        seconds_ago = int((datetime.utcnow() - s.last_activity).total_seconds())
        out.append(schemas.CandidateStatusOut(
            session_id=s.id,
            user_id=s.user_id,
            full_name=s.user.full_name,
            email=s.user.email,
            status=s.status.value,
            strikes=s.strikes,
            max_strikes=MAX_STRIKES,
            answered_count=answered,
            total_questions=len(s.question_order or []),
            time_remaining_seconds=exam_engine.time_remaining_seconds(s),
            last_activity_seconds_ago=seconds_ago,
            score=s.score,
            domain_selected=s.domain_selected,
            paper2_domain=s.paper2_domain,
            current_phase=s.current_phase,
            phase1_submitted_at=s.phase1_submitted_at,
            phase2_submitted_at=s.phase2_submitted_at,
            submitted_at=s.submitted_at,
            started_at=s.started_at,
        ))
    return out


# ============================================================
# Violations for a candidate (admin-only)
# ============================================================

@router.get("/admin/candidates/{user_id}/violations", response_model=List[schemas.ViolationDetailOut])
def get_candidate_violations(
    user_id: int,
    db: Session = Depends(get_db),
    admin: models.User = Depends(security.get_current_admin),
):
    """Return the violation timeline for a specific candidate. Admin-only."""
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user or user.role != models.UserRole.candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    session = (
        db.query(models.ExamSession)
        .filter(models.ExamSession.user_id == user_id)
        .order_by(models.ExamSession.id.desc())
        .first()
    )
    if not session:
        return []
    violations = (
        db.query(models.Violation)
        .filter(models.Violation.session_id == session.id)
        .order_by(models.Violation.created_at)
        .all()
    )
    return [
        schemas.ViolationDetailOut(
            id=v.id,
            violation_type=v.violation_type,
            strike_number=v.strike_number,
            created_at=v.created_at,
        )
        for v in violations
    ]


# ============================================================
# Admin: Disqualify candidate
# ============================================================

@router.post("/admin/candidates/{user_id}/disqualify", response_model=schemas.DisqualifyResponse)
async def disqualify_candidate(
    user_id: int,
    payload: schemas.DisqualifyRequest,
    db: Session = Depends(get_db),
    admin: models.User = Depends(security.get_current_admin),
):
    """
    Admin-only: disqualify a candidate. Saves existing answers, grades what
    is available, revokes the candidate's JWT immediately.
    Violations NEVER auto-disqualify — only this endpoint can.
    """
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user or user.role != models.UserRole.candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    session = (
        db.query(models.ExamSession)
        .filter(models.ExamSession.user_id == user_id)
        .order_by(models.ExamSession.id.desc())
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="No exam session found for this candidate")

    session = exam_engine.admin_disqualify(db, session, user)

    # Notify the candidate's browser session immediately (if connected)
    await manager.send_to_candidate(user_id, {
        "type": "exam_disqualified",
        "message": "You have been disqualified by the examination administrator.",
    })
    await _broadcast_admin_update(db, session, user)

    return schemas.DisqualifyResponse(
        user_id=user.id,
        email=user.email,
        message="Candidate disqualified. Their session has been closed and token revoked.",
    )


async def _broadcast_admin_update(db, session, user):
    """Broadcast updated candidate state to admin dashboards after admin action."""
    nav = exam_engine.build_nav(db, session)
    answered = sum(1 for n in nav if n["answered"])
    await manager.broadcast({
        "type": "candidate_update",
        "session_id": session.id,
        "user_id": user.id,
        "full_name": user.full_name,
        "email": user.email,
        "status": session.status.value,
        "strikes": session.strikes,
        "max_strikes": MAX_STRIKES,
        "answered_count": answered,
        "total_questions": len(session.question_order or []),
        "time_remaining_seconds": 0,
        "domain_selected": session.domain_selected,
        "paper2_domain": session.paper2_domain,
        "current_phase": session.current_phase,
    })


# ============================================================
# Admin: Full result for a candidate (admin-only)
# ============================================================

@router.get("/admin/candidates/{user_id}/result", response_model=schemas.CandidateFullResultOut)
def get_candidate_result(
    user_id: int,
    db: Session = Depends(get_db),
    admin: models.User = Depends(security.get_current_admin),
):
    """
    Admin-only: full result breakdown for a candidate.
    Candidates attempting this endpoint receive 403 Forbidden.
    """
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user or user.role != models.UserRole.candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    session = (
        db.query(models.ExamSession)
        .filter(models.ExamSession.user_id == user_id)
        .order_by(models.ExamSession.id.desc())
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="No exam session found")

    results = (
        db.query(models.CandidateResult)
        .filter(models.CandidateResult.session_id == session.id)
        .all()
    )
    results_map = {r.phase: r for r in results}

    violations = (
        db.query(models.Violation)
        .filter(models.Violation.session_id == session.id)
        .order_by(models.Violation.created_at)
        .all()
    )

    def build_phase_out(r: models.CandidateResult) -> schemas.PhaseResultOut:
        detail = None
        if r.question_detail:
            detail = [schemas.QuestionResultItem(**item) for item in r.question_detail]
        return schemas.PhaseResultOut(
            phase=r.phase, domain=r.domain,
            total_questions=r.total_questions, attempted=r.attempted,
            correct=r.correct, incorrect=r.incorrect, unanswered=r.unanswered,
            score=r.score, max_score=r.max_score, accuracy=r.accuracy,
            submitted_at=r.submitted_at, question_detail=detail,
        )

    p1 = build_phase_out(results_map[1]) if 1 in results_map else None
    p2 = build_phase_out(results_map[2]) if 2 in results_map else None

    total_q = (p1.total_questions if p1 else 0) + (p2.total_questions if p2 else 0)
    total_att = (p1.attempted if p1 else 0) + (p2.attempted if p2 else 0)
    total_cor = (p1.correct if p1 else 0) + (p2.correct if p2 else 0)
    total_inc = (p1.incorrect if p1 else 0) + (p2.incorrect if p2 else 0)
    total_una = (p1.unanswered if p1 else 0) + (p2.unanswered if p2 else 0)
    total_sc  = (p1.score if p1 else 0) + (p2.score if p2 else 0)
    total_max = (p1.max_score if p1 else 0) + (p2.max_score if p2 else 0)
    overall_acc = round((total_cor / total_max) * 100, 2) if total_max > 0 else None

    violation_list = [
        {"id": v.id, "violation_type": v.violation_type,
         "strike_number": v.strike_number, "created_at": v.created_at.isoformat()}
        for v in violations
    ]

    return schemas.CandidateFullResultOut(
        user_id=user.id, full_name=user.full_name, email=user.email,
        session_id=session.id, session_status=session.status.value,
        started_at=session.started_at, submitted_at=session.submitted_at,
        domain=session.paper2_domain,
        phase1=p1, phase2=p2,
        total_questions=total_q, total_attempted=total_att,
        total_correct=total_cor, total_incorrect=total_inc,
        total_unanswered=total_una, total_score=total_sc,
        total_max_score=total_max, overall_accuracy=overall_acc,
        violations=violation_list,
    )


# ============================================================
# Admin: Generate result PDF (admin-only)
# ============================================================

@router.get("/admin/candidates/{user_id}/result/pdf")
def get_candidate_result_pdf(
    user_id: int,
    db: Session = Depends(get_db),
    admin: models.User = Depends(security.get_current_admin),
):
    """
    Admin-only: generate and return a PDF result report.
    Candidates attempting this endpoint receive 403 Forbidden.
    Returns application/pdf with Content-Disposition: attachment.
    """
    # Reuse the result endpoint logic
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user or user.role != models.UserRole.candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")

    session = (
        db.query(models.ExamSession)
        .filter(models.ExamSession.user_id == user_id)
        .order_by(models.ExamSession.id.desc())
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="No exam session found")

    results = (
        db.query(models.CandidateResult)
        .filter(models.CandidateResult.session_id == session.id)
        .all()
    )
    global_state = exam_engine.get_global_exam_state(db)

    try:
        pdf_bytes = _generate_result_pdf(user, session, results, global_state)
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="PDF generation library not installed. Run: pip install reportlab",
        )

    safe_name = user.full_name.replace(" ", "_").lower()
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="result_{safe_name}.pdf"'},
    )


def _generate_result_pdf(
    user: models.User,
    session: models.ExamSession,
    results: list,
    global_state: models.ExamGlobalState,
) -> bytes:
    """Generate a professional result PDF using reportlab."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
    )
    from reportlab.lib.enums import TA_CENTER, TA_LEFT

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        rightMargin=2*cm, leftMargin=2*cm,
        topMargin=2.5*cm, bottomMargin=2*cm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("title", parent=styles["Heading1"], alignment=TA_CENTER,
                                 fontSize=16, spaceAfter=4, textColor=colors.HexColor("#1a1a2e"))
    sub_style   = ParagraphStyle("sub", parent=styles["Normal"], alignment=TA_CENTER,
                                 fontSize=11, spaceAfter=2, textColor=colors.HexColor("#444"))
    label_style = ParagraphStyle("label", parent=styles["Normal"], fontSize=9,
                                 textColor=colors.HexColor("#666"), spaceBefore=2)
    value_style = ParagraphStyle("value", parent=styles["Normal"], fontSize=11,
                                 textColor=colors.HexColor("#111"), spaceBefore=0)
    section_style = ParagraphStyle("section", parent=styles["Heading2"], fontSize=13,
                                   textColor=colors.HexColor("#1a1a2e"), spaceBefore=16, spaceAfter=6)
    small_style = ParagraphStyle("small", parent=styles["Normal"], fontSize=8,
                                 textColor=colors.HexColor("#888"))

    results_map = {r.phase: r for r in results}

    def fmt_dt(dt):
        if not dt:
            return "—"
        # Convert UTC to IST for display (UTC+5:30)
        from datetime import timedelta
        ist = dt + timedelta(hours=5, minutes=30)
        return ist.strftime("%d %b %Y  %I:%M:%S %p IST")

    def row_table(data, col_widths=None):
        t = Table(data, colWidths=col_widths)
        t.setStyle(TableStyle([
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#555")),
            ("TEXTCOLOR", (1, 0), (1, -1), colors.HexColor("#111")),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ]))
        return t

    story = []

    # Header
    story.append(Paragraph("Dhanalakshmi Srinivasan Institute of Research and Technology", title_style))
    story.append(Paragraph("Ph.D Entrance Examination", sub_style))
    story.append(Paragraph("Official Result Certificate", ParagraphStyle("cert", parent=sub_style,
                            fontSize=10, textColor=colors.HexColor("#888"))))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#ddd"), spaceAfter=12))

    # Candidate info
    story.append(Paragraph("Candidate Information", section_style))
    scheduled_start = fmt_dt(global_state.scheduled_start_at)
    scheduled_end = fmt_dt(global_state.scheduled_end_at)
    time_used = "—"
    if session.started_at and session.submitted_at:
        secs = int((session.submitted_at - session.started_at).total_seconds())
        h, rem = divmod(secs, 3600); m, s = divmod(rem, 60)
        time_used = f"{h:02d}:{m:02d}:{s:02d}"

    info_data = [
        ["Candidate Name",  user.full_name],
        ["Email",           user.email],
        ["Domain (Phase 2)", session.paper2_domain or "—"],
        ["Scheduled Start",  scheduled_start],
        ["Scheduled End",    scheduled_end],
        ["Actual Start",     fmt_dt(session.started_at)],
        ["Actual Completion", fmt_dt(session.submitted_at)],
        ["Time Used",        time_used],
        ["Status",           session.status.value.replace("_", " ").title()],
    ]
    story.append(row_table(info_data, [5*cm, 11*cm]))
    story.append(Spacer(1, 0.3*cm))

    def phase_table(r: models.CandidateResult, label: str):
        story.append(Paragraph(label, section_style))
        data = [
            ["Total Questions",  str(r.total_questions)],
            ["Attempted",        str(r.attempted)],
            ["Correct",          str(r.correct)],
            ["Incorrect",        str(r.incorrect)],
            ["Unanswered",       str(r.unanswered)],
            ["Score",            f"{r.score} / {r.max_score}"],
            ["Accuracy",         f"{r.accuracy:.1f}%" if r.accuracy is not None else "—"],
        ]
        story.append(row_table(data, [5*cm, 11*cm]))

    if 1 in results_map:
        phase_table(results_map[1], "Phase 1 — Research Methodology")
    if 2 in results_map:
        phase_table(results_map[2], f"Phase 2 — {results_map[2].domain or 'Domain Specific'}")

    # Combined totals
    p1 = results_map.get(1)
    p2 = results_map.get(2)
    total_q = (p1.total_questions if p1 else 0) + (p2.total_questions if p2 else 0)
    total_sc = (p1.score if p1 else 0) + (p2.score if p2 else 0)
    total_mx = (p1.max_score if p1 else 0) + (p2.max_score if p2 else 0)
    total_cor = (p1.correct if p1 else 0) + (p2.correct if p2 else 0)
    total_inc = (p1.incorrect if p1 else 0) + (p2.incorrect if p2 else 0)
    total_una = (p1.unanswered if p1 else 0) + (p2.unanswered if p2 else 0)
    total_att = (p1.attempted if p1 else 0) + (p2.attempted if p2 else 0)
    overall_acc = round((total_cor / total_mx) * 100, 2) if total_mx > 0 else 0

    story.append(Paragraph("Final Combined Result", section_style))
    final_data = [
        ["Total Questions",  str(total_q)],
        ["Attempted",        str(total_att)],
        ["Correct",          str(total_cor)],
        ["Incorrect",        str(total_inc)],
        ["Unanswered",       str(total_una)],
        ["Total Score",      f"{total_sc} / {total_mx}"],
        ["Overall Accuracy", f"{overall_acc:.1f}%"],
    ]
    story.append(row_table(final_data, [5*cm, 11*cm]))

    # Question-wise breakdown
    for phase_num in [1, 2]:
        if phase_num not in results_map:
            continue
        r = results_map[phase_num]
        if not r.question_detail:
            continue
        label = "Phase 1 — Question-wise Breakdown" if phase_num == 1 else \
                f"Phase 2 — Question-wise Breakdown ({r.domain})"
        story.append(Paragraph(label, section_style))

        qw_data = [["#", "Question", "Your Answer", "Correct Answer", "Result"]]
        for i, item in enumerate(r.question_detail, 1):
            status_str = "✓ Correct" if item["is_correct"] else ("✗ Incorrect" if item["is_answered"] else "— Unanswered")
            q_text = (item["question_text"] or "")[:80] + ("…" if len(item.get("question_text","")) > 80 else "")
            qw_data.append([
                str(i),
                Paragraph(q_text, small_style),
                Paragraph(str(item.get("candidate_answer_text") or "—"), small_style),
                Paragraph(str(item.get("correct_answer_text") or "—"), small_style),
                status_str,
            ])

        qw_table = Table(qw_data, colWidths=[0.8*cm, 6.5*cm, 3.2*cm, 3.2*cm, 2.3*cm])
        qw_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#ddd")),
        ]))
        story.append(qw_table)
        story.append(Spacer(1, 0.4*cm))

    # Footer
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#ccc"), spaceBefore=12))
    gen_time = datetime.utcnow().strftime("%d %b %Y %H:%M UTC")
    story.append(Paragraph(
        f"Generated by Ph.D Entrance Examination System · {gen_time}",
        ParagraphStyle("footer", parent=styles["Normal"], alignment=TA_CENTER,
                       fontSize=8, textColor=colors.HexColor("#aaa")),
    ))

    doc.build(story)
    return buffer.getvalue()


# ============================================================
# Bulk results export — single-click "Download All Results" (CSV/Excel)
# ============================================================

_RESULTS_EXPORT_COLUMNS = [
    "Full Name", "Email", "Session Status", "Domain (Phase 2)",
    "Started At (UTC)", "Submitted At (UTC)",
    "Phase 1 Score", "Phase 1 Max", "Phase 1 Accuracy (%)",
    "Phase 2 Score", "Phase 2 Max", "Phase 2 Accuracy (%)",
    "Total Score", "Total Max", "Overall Accuracy (%)",
    "Violation Count",
]


def _collect_results_rows(db: Session) -> list:
    """
    One row per candidate (their most recent exam session, if any) — the
    same numbers the per-candidate Result modal/PDF show, just flattened
    for a spreadsheet. Candidates who never attempted the exam are still
    included with blank/zero fields so the export is a genuine roster,
    not just a list of completers.

    Built with a handful of bulk queries (not one query per candidate) so
    this stays cheap even with hundreds of candidates.
    """
    candidates = (
        db.query(models.User)
        .filter(models.User.role == models.UserRole.candidate)
        .order_by(models.User.full_name)
        .all()
    )
    if not candidates:
        return []

    # Latest session per candidate (highest id == most recent attempt).
    latest_ids_sub = (
        db.query(func.max(models.ExamSession.id).label("max_id"))
        .group_by(models.ExamSession.user_id)
        .subquery()
    )
    sessions = (
        db.query(models.ExamSession)
        .filter(models.ExamSession.id.in_(db.query(latest_ids_sub.c.max_id)))
        .all()
    )
    session_by_user = {s.user_id: s for s in sessions}
    session_ids = [s.id for s in sessions]

    results_by_session: dict = {}
    if session_ids:
        for r in db.query(models.CandidateResult).filter(models.CandidateResult.session_id.in_(session_ids)).all():
            results_by_session.setdefault(r.session_id, {})[r.phase] = r

    rows = []
    for user in candidates:
        session = session_by_user.get(user.id)
        row = {
            "Full Name": user.full_name,
            "Email": user.email,
            "Session Status": "no_attempt",
            "Domain (Phase 2)": "",
            "Started At (UTC)": "",
            "Submitted At (UTC)": "",
            "Phase 1 Score": "", "Phase 1 Max": "", "Phase 1 Accuracy (%)": "",
            "Phase 2 Score": "", "Phase 2 Max": "", "Phase 2 Accuracy (%)": "",
            "Total Score": "", "Total Max": "", "Overall Accuracy (%)": "",
            "Violation Count": "",
        }
        if session:
            phase_results = results_by_session.get(session.id, {})
            p1 = phase_results.get(1)
            p2 = phase_results.get(2)
            total_score = (p1.score if p1 else 0) + (p2.score if p2 else 0)
            total_max = (p1.max_score if p1 else 0) + (p2.max_score if p2 else 0)
            overall_acc = round((total_score / total_max) * 100, 2) if total_max > 0 else ""
            row.update({
                "Session Status": session.status.value,
                "Domain (Phase 2)": session.paper2_domain or "",
                "Started At (UTC)": session.started_at.isoformat() if session.started_at else "",
                "Submitted At (UTC)": session.submitted_at.isoformat() if session.submitted_at else "",
                "Phase 1 Score": p1.score if p1 else "",
                "Phase 1 Max": p1.max_score if p1 else "",
                "Phase 1 Accuracy (%)": p1.accuracy if p1 else "",
                "Phase 2 Score": p2.score if p2 else "",
                "Phase 2 Max": p2.max_score if p2 else "",
                "Phase 2 Accuracy (%)": p2.accuracy if p2 else "",
                "Total Score": total_score if (p1 or p2) else "",
                "Total Max": total_max if (p1 or p2) else "",
                "Overall Accuracy (%)": overall_acc,
                "Violation Count": session.strikes,
            })
        rows.append(row)
    return rows


@router.get("/admin/results/export")
def export_all_results(
    format: str = Query("xlsx", pattern="^(xlsx|csv)$"),
    db: Session = Depends(get_db),
    admin: models.User = Depends(security.get_current_admin),
):
    """
    Single-click bulk export of every candidate's results — the button on
    the Admin Dashboard. `?format=xlsx` (default) or `?format=csv`.
    """
    rows = _collect_results_rows(db)

    if format == "csv":
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=_RESULTS_EXPORT_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
        return Response(
            content=buf.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": "attachment; filename=all_candidate_results.csv"},
        )

    try:
        import openpyxl
        from openpyxl.styles import Font
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="Excel export library not installed. Run: pip install openpyxl (or use ?format=csv).",
        )

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "All Results"
    ws.append(_RESULTS_EXPORT_COLUMNS)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in rows:
        ws.append([row[col] for col in _RESULTS_EXPORT_COLUMNS])
    for col_cells in ws.columns:
        length = max((len(str(c.value)) if c.value is not None else 0) for c in col_cells)
        ws.column_dimensions[col_cells[0].column_letter].width = min(max(length + 2, 10), 40)

    out = io.BytesIO()
    wb.save(out)
    return Response(
        content=out.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=all_candidate_results.xlsx"},
    )


# ============================================================
# Grant Retake
# ============================================================

@router.post("/admin/users/{user_id}/grant-retake", response_model=schemas.GrantRetakeResponse)
async def grant_retake(
    user_id: int,
    db: Session = Depends(get_db),
    admin: models.User = Depends(security.get_current_admin),
):
    user = db.query(models.User).filter(models.User.id == user_id).first()
    if not user or user.role != models.UserRole.candidate:
        raise HTTPException(status_code=404, detail="Candidate not found")
    session = (
        db.query(models.ExamSession)
        .filter(models.ExamSession.user_id == user_id)
        .order_by(models.ExamSession.id.desc())
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="No exam session found for this candidate")

    session.status = models.SessionStatus.in_progress
    session.strikes = 0
    session.retake_granted = True
    user.is_active = True
    user.token_version = (user.token_version or 0) + 1
    db.add(session); db.add(user); db.commit()

    await manager.send_to_candidate(user_id, {
        "type": "retake_granted",
        "message": "The administrator has granted you re-entry. Please re-enter the exam.",
    })
    return schemas.GrantRetakeResponse(
        user_id=user.id, email=user.email,
        message="Retake granted. Candidate's infractions reset to 0.",
    )


# ============================================================
# Question bank upload
# ============================================================

@router.post("/admin/questions/upload")
async def upload_questions(
    file: UploadFile = File(...),
    domain: Optional[str] = Form(None),
    phase: Optional[int] = Form(None),
    db: Session = Depends(get_db),
    admin: models.User = Depends(security.get_current_admin),
):
    """
    One upload endpoint for the whole question bank:

      .zip  — a bundle of .csv files, one per domain. Each member's domain
              is inferred from its filename (e.g. "agronomy.csv" ->
              Agricultural - Agronomy); the domain/phase form fields are
              only used as a fallback for a member whose name doesn't
              match anything, or when phase can't be inferred.
      .csv  — a single file. `domain` is required unless the CSV itself
              has a 'domain' column; `phase` defaults from the domain
              (Research Methodology -> 1, everything else -> 2) if omitted.
      .docx / .xlsx — legacy single-domain formats; both domain and phase
              are required for these.

    The whole upload is one DB transaction: if the file itself can't be
    parsed (corrupt zip, undecodable bytes, unsupported extension), NOTHING
    is inserted. Individual bad rows inside an otherwise-good file are
    skipped and reported by row number (and source filename, for a zip) —
    every valid row still gets committed.
    """
    contents = await file.read()
    clean_domain = (domain or "").strip() or None
    next_order_no = db.query(models.Question).count() + 1

    try:
        questions, row_errors = parse_upload_file(
            file.filename, contents,
            default_domain=clean_domain, default_phase=phase,
            start_order_no=next_order_no, domains_allowlist=DOMAINS,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to process file: {e}")

    if not questions and not row_errors:
        raise HTTPException(status_code=400, detail="No questions were found in the file.")

    if questions:
        db.add_all(questions)
        db.commit()

    return {
        "status": "success" if questions else "no_questions_imported",
        "inserted": len(questions),
        "errors": [{"row": e.row, "message": e.message, "source": e.source} for e in row_errors],
    }


# ============================================================
# Candidate CSV bulk upload
# ============================================================

@router.post("/admin/users/upload")
async def upload_users(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    admin: models.User = Depends(security.get_current_admin),
):
    contents = await file.read()
    text = contents.decode("utf-8")
    reader = csv.DictReader(io.StringIO(text))
    inserted = 0
    default_hash = security.hash_password(CANDIDATE_DEFAULT_PASSWORD)
    for row in reader:
        email = row.get("email", "").strip()
        name = row.get("name", "").strip()
        if not email or not name:
            continue
        existing = db.query(models.User).filter(models.User.email == email).first()
        if not existing:
            db.add(models.User(
                email=email, full_name=name, hashed_password=default_hash,
                role=models.UserRole.candidate, approval_status="approved",
                must_reset_password=False, is_active=True,
            ))
            inserted += 1
    db.commit()
    return {"status": "success", "inserted": inserted}


# ============================================================
# Admin WebSocket
# ============================================================

ADMIN_WS_IDLE_TIMEOUT_SECONDS = 30


@router.websocket("/ws/admin")
async def admin_ws(websocket: WebSocket, token: str = Query(...)):
    db = SessionLocal()
    try:
        security.decode_token_for_ws(token, db)
    except Exception:
        await websocket.close(code=4401)
        return
    finally:
        db.close()

    connected = False
    try:
        await manager.connect(websocket)
        connected = True

        while True:
            try:
                raw = await asyncio.wait_for(
                    websocket.receive_text(), timeout=ADMIN_WS_IDLE_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                # No inbound signaling frame in the window — perfectly
                # normal (the admin console mostly just *receives*
                # broadcasts). Loop back around rather than treating an
                # idle admin dashboard as a dead connection.
                continue

            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(data, dict):
                continue

            if data.get("type") in ["offer", "answer", "ice_candidate"]:
                target_user_id = data.get("target_user_id")
                if target_user_id:
                    await manager.send_to_candidate(target_user_id, data)

    except WebSocketDisconnect:
        pass

    except Exception:
        # A malformed frame, a reset connection, or any other transport
        # hiccup on ONE admin's socket must never crash the worker or
        # disrupt other admins' or candidates' connections.
        pass

    finally:
        if connected:
            try:
                await manager.disconnect(websocket)
            except Exception:
                pass
