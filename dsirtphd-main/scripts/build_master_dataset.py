import csv
import json
import re

def build_all():
    all_questions = []
    current_id = 1

    # 1. Read data/prompt_questions.csv
    with open("data/prompt_questions.csv", "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            domain = row["domain"].strip()
            phase = int(row["phase"].strip())
            q_text = row["question_text"].strip()
            options = [
                row["option_a"].strip(),
                row["option_b"].strip(),
                row["option_c"].strip(),
                row["option_d"].strip()
            ]
            correct_opt = int(row["correct_option"].strip())

            all_questions.append({
                "id": current_id,
                "order_no": len([q for q in all_questions if q["domain"] == domain]) + 1,
                "domain": domain,
                "phase": phase,
                "question_type": "mcq",
                "question_text": q_text,
                "options": options,
                "correct_option": correct_opt
            })
            current_id += 1

    # 2. Add Commerce & Management Studies
    commerce_answers = [
        2, 1, 1, 0, 1, 1, 1, 1, 1, 1, # 1-10
        1, 1, 1, 3, 1, 1, 1, 0, 1, 1, # 11-20
        1, 0, 1, 1, 1, 1, 1, 1, 0, 2, # 21-30
        2, 3, 1, 1, 0, 0, 1, 0, 0, 1, # 31-40
        0, 1, 0, 0, 1, 1, 0, 1, 1, 1  # 41-50
    ]

    with open("questions files/PhD_Entrance_Exam_Commerce_Management_Alternate_Questions.csv", "r", encoding="utf-8", errors="ignore") as f:
        lines = [l.strip().strip("\"") for l in f if l.strip()]

    q_list = []
    curr = None
    for l in lines:
        m = re.match(r"^(\d+)\.\s*(.*)", l)
        if m:
            if curr: q_list.append(curr)
            curr = {"num": int(m.group(1)), "text": m.group(2), "options": []}
        elif curr:
            opt_m = re.match(r"^[A-D]\)\s*(.*)", l)
            if opt_m:
                curr["options"].append(opt_m.group(1).strip())
    if curr: q_list.append(curr)

    for i, q in enumerate(q_list):
        all_questions.append({
            "id": current_id,
            "order_no": i + 1,
            "domain": "Commerce & Management Studies",
            "phase": 2,
            "question_type": "mcq",
            "question_text": q["text"],
            "options": q["options"],
            "correct_option": commerce_answers[i] if i < len(commerce_answers) else 0
        })
        current_id += 1

    # Save to data/master_questions.json
    with open("data/master_questions.json", "w", encoding="utf-8") as f:
        json.dump(all_questions, f, ensure_ascii=False, indent=2)

    # Verification
    domains = {}
    for q in all_questions:
        domains[q["domain"]] = domains.get(q["domain"], 0) + 1

    print(f"Total questions generated: {len(all_questions)}")
    for d, c in sorted(domains.items()):
        print(f"  - {d}: {c} questions")

if __name__ == "__main__":
    build_all()
