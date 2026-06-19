import argparse
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from app.database import SessionLocal
from app.init_db import init_db
from app.models import User, UserRole
from app.security import hash_password
from app.services.drivers import get_or_create_company


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a company manager account")
    parser.add_argument("username")
    parser.add_argument("password")
    parser.add_argument("company")
    args = parser.parse_args()

    init_db()
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.username == args.username)):
            raise SystemExit(f"User {args.username!r} already exists")
        company = get_or_create_company(db, args.company)
        db.add(
            User(
                username=args.username,
                password_hash=hash_password(args.password),
                role=UserRole.MANAGER,
                company_id=company.id,
            )
        )
        db.commit()
        print(f"Created manager {args.username!r} for {company.name!r}")


if __name__ == "__main__":
    main()
