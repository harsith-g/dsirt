"""
Application startup seed.

Creates missing tables, safely seeds the admin account, imports
missing candidates from the XLSX roster, and loads missing question banks.
"""
import os
import openpyxl
from backend.database import SessionLocal, engine, Base
from backend import models
from backend.security import hash_password
from backend.config import CANDIDATE_DEFAULT_PASSWORD, ADMIN_EMAIL, ADMIN_PASSWORD
from backend.services.ingestion import parse_docx_questions

ROSTER_XLSX = "Application Details (23).xlsx"
MAX_CANDIDATES = 500

DOMAIN_MAPPING = {
    "Research Methodology": "DSU_Research_Methodology_Set_II.docx",
    "Mathematics": "MATHS.docx",
    "Tamil": "Ph.D - ENTRANCE EXAM  TAMILTAMILQuestion.doc",
    "Agricultural - Agronomy": "AGRONOMY -Final.docx",
    "Bio-Science": "LIF SCIENCE.docx",
    "Agricultural - Entomology": "entomology.docx",
    "Agricultural - Pathology": "Pathology.docx",
    "English": "PhD- Entrance  Eng QP.docx",
    "Commerce & Management Studies": "PhD_Entrance_Exam_Commerce_Management_Alternate_Questions.docx",
    "Computer Science": "COMPUTER SCIENCE ENTRANCE TEST QUESTIONS.docx",
}

def seed():
    # Only create tables if they do not exist
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    
    total_seeded = 0
    try:
        # --- Admin account ---
        existing_admin = db.query(models.User).filter_by(email=ADMIN_EMAIL).first()
        if not existing_admin:
            admin = models.User(
                email=ADMIN_EMAIL,
                full_name="Exam Administrator",
                hashed_password=hash_password(ADMIN_PASSWORD),
                role=models.UserRole.admin,
                approval_status="approved",
                must_reset_password=False,
            )
            db.add(admin)

        # --- Candidates from XLSX roster ---
        candidate_hash = hash_password(CANDIDATE_DEFAULT_PASSWORD)
        count = 0
        if os.path.exists(ROSTER_XLSX):
            wb = openpyxl.load_workbook(ROSTER_XLSX)
            sheet = wb.active
            start_reading = False
            for row in sheet.iter_rows(values_only=True):
                if not row[0]: continue
                if str(row[0]).strip() == 'S.No':
                    start_reading = True
                    continue
                if start_reading:
                    try: int(row[0])
                    except (ValueError, TypeError): continue
                    
                    first_name = str(row[3] or "").strip()
                    last_name = str(row[5] or "").strip()
                    email = str(row[7] or "").strip()
                    if email and email.lower() != "none" and "@" in email:
                        existing_candidate = db.query(models.User).filter_by(email=email).first()
                        if not existing_candidate:
                            db.add(models.User(
                                email=email,
                                full_name=f"{first_name} {last_name}".strip(),
                                hashed_password=candidate_hash,
                                role=models.UserRole.candidate,
                                approval_status="approved",
                                must_reset_password=False,
                            ))
                            count += 1
                    if count >= MAX_CANDIDATES: break
        else:
            print(f"[SEED][WARNING] {ROSTER_XLSX} not found — no candidates seeded.")

        # --- Global exam state ---
        existing_state = db.query(models.ExamGlobalState).filter_by(id=1).first()
        if not existing_state:
            db.add(models.ExamGlobalState(id=1, exam_open=False))
        
        db.commit() # commit users so we have them
        
        # --- Question banks ---
        print("\n========== QUESTION SEED SUMMARY ==========")
        for domain, filename in DOMAIN_MAPPING.items():
            file_path = os.path.join("files_questiond", filename)
            if not os.path.exists(file_path):
                print(f"[SEED][ERROR] {domain} -> {filename} -> File not found")
                continue
                
            try:
                with open(file_path, "rb") as f:
                    file_bytes = f.read()
                    
                phase = 1 if domain == "Research Methodology" else 2
                questions = parse_docx_questions(file_bytes, domain, phase)
                
                # Check against existing questions
                existing_questions = db.query(models.Question).filter_by(domain=domain, phase=phase).all()
                existing_texts = {q.question_text for q in existing_questions}
                
                new_questions = []
                for q in questions:
                    if q.question_text not in existing_texts:
                        new_questions.append(q)
                
                if new_questions:
                    db.add_all(new_questions)
                    db.commit()
                
                q_len = len(new_questions)
                total_seeded += q_len
                print(f"[SEED] {domain} -> {filename} -> {q_len} questions inserted (skipped {len(questions) - q_len} existing)")
                
            except Exception as e:
                db.rollback()
                print(f"[SEED][ERROR] {domain} -> {filename} -> {e}")
                
        print(f"Total new questions seeded: {total_seeded}")
        print("===========================================\n")
        
    except Exception as e:
        db.rollback()
        print(f"[SEED] FATAL ERROR: {e}")
        raise
    finally:
        db.close()

if __name__ == "__main__":
    seed()
