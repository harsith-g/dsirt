import os
import sys

# Add project root to path so we can import models and DB
sys.path.append('.')
from backend.database import SessionLocal
from backend.models import Question, QuestionType
from backend.services.ingestion import parse_docx_questions

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

def run_import():
    db = SessionLocal()
    
    print("Clearing existing questions...")
    db.query(Question).delete()
    db.commit()
    
    total_imported = 0
    folder = 'files_questiond'
    
    for domain, filename in DOMAIN_MAPPING.items():
        filepath = os.path.join(folder, filename)
        if not os.path.exists(filepath):
            print(f"[ERROR] {domain} -> {filename} -> File not found")
            continue
            
        phase = 1 if domain == "Research Methodology" else 2
        
        try:
            with open(filepath, "rb") as f:
                file_bytes = f.read()
                
            questions = parse_docx_questions(file_bytes, domain, phase)
            
            db.add_all(questions)
            total_imported += len(questions)
            
            print(f"File: {filename} -> Parsed {len(questions)} questions for {domain} (Phase {phase})")
        except Exception as e:
            print(f"[ERROR] failed to parse {filename}: {e}")
            
    db.commit()
    db.close()
    print(f"\nSuccessfully imported {total_imported} total questions!")

if __name__ == '__main__':
    run_import()

