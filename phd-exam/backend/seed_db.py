"""
Standalone seed script for adding/updating specific accounts without wiping the database.

Usage:
    python backend/seed_db.py

All candidate accounts are seeded with the universal password: dsirt@123
No password reset is required on first login.
"""
import sys
import os

# Add the root directory to PYTHONPATH to allow running directly from backend/
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from backend.database import SessionLocal, engine, Base
from backend import models
from backend.security import hash_password
from backend.config import CANDIDATE_DEFAULT_PASSWORD, ADMIN_EMAIL, ADMIN_PASSWORD


def seed_accounts():
    """
    Idempotent account seeder — creates accounts that don't exist yet.
    Only seeds the admin and a clean set of accounts.
    All candidates use dsirt@123.
    """
    # create_all safely creates tables if they don't exist without wiping data
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    accounts_to_seed = [
        {
            "email": ADMIN_EMAIL,
            "full_name": "Exam Administrator",
            "password": ADMIN_PASSWORD,
            "role": models.UserRole.admin,
            "approval_status": "approved",
            "must_reset_password": False,
        },
    ]

    try:
        count = 0
        for acc in accounts_to_seed:
            existing = db.query(models.User).filter(models.User.email == acc["email"]).first()
            if not existing:
                user = models.User(
                    email=acc["email"],
                    full_name=acc["full_name"],
                    hashed_password=hash_password(acc["password"]),
                    role=acc["role"],
                    approval_status=acc.get("approval_status", "approved"),
                    must_reset_password=acc["must_reset_password"],
                )
                db.add(user)
                count += 1
                print(f"  Created: {acc['email']}")
            else:
                print(f"  Already exists: {acc['email']}")

        # Ensure global exam state row exists
        existing_state = db.query(models.ExamGlobalState).filter(models.ExamGlobalState.id == 1).first()
        if not existing_state:
            db.add(models.ExamGlobalState(id=1, exam_open=False))
            print("  Created: ExamGlobalState (exam_open=False)")
        else:
            print("  Already exists: ExamGlobalState")

        db.commit()
        print(f"\nSeeding complete. Added {count} new account(s).")
    except Exception as e:
        print(f"Error seeding database: {e}")
        db.rollback()
    finally:
        db.close()


if __name__ == "__main__":
    print("=== PhD Examination Platform — Account Seeder ===")
    seed_accounts()
