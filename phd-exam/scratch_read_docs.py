import os
import docx

folder = "x:/VS code projects/project_export/files_questiond"
files = [
    "AGRONOMY -Final.docx",
    "COMPUTER SCIENCE ENTRANCE TEST QUESTIONS.docx",
    "DSU_Research_Methodology_Set_II.docx",
    "LIF SCIENCE.docx",
    "MATHS.docx",
    "Pathology.docx",
    "Ph.D - ENTRANCE EXAM  TAMILTAMILQuestion.doc",
    "PhD- Entrance  Eng QP.docx",
    "PhD_Entrance_Exam_Commerce_Management_Alternate_Questions.docx",
    "entomology.docx"
]

with open("scratch_docs_output.txt", "w", encoding="utf-8") as out:
    for filename in files:
        filepath = os.path.join(folder, filename)
        out.write(f"=== {filename} ===\n")
        try:
            if filename.endswith(".doc"):
                out.write("Cannot read .doc file with python-docx\n\n")
                continue
                
            doc = docx.Document(filepath)
            lines = []
            for i, para in enumerate(doc.paragraphs):
                text = para.text.strip()
                if text:
                    lines.append(text)
                if len(lines) >= 30:
                    break
                    
            for line in lines:
                out.write(line + "\n")
        except Exception as e:
            out.write(f"Error: {e}\n")
        out.write("\n\n")
