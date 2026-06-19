import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal
from app.init_db import init_db
from app.services.drive_sync import scan_drive_to_database


def main() -> None:
    init_db()
    with SessionLocal() as db:
        result = scan_drive_to_database(db, verbose=True)
        print(result)


if __name__ == "__main__":
    main()
