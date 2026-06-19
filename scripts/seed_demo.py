from datetime import date
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.database import SessionLocal
from app.init_db import init_db
from app.models import DocumentType, DriverStatus
from app.services.drivers import add_document_record, upsert_driver


def main() -> None:
    init_db()
    with SessionLocal() as db:
        driver = upsert_driver(
            db,
            company_name="Demo Logistics",
            telegram_id=100100100,
            telegram_username="demo_driver",
            full_name="John Smith",
            birth_date=date(1988, 5, 17),
            phone="+1 555 0100",
            license_number="D1234567",
        )
        driver.status = DriverStatus.MISSING_DOCS
        add_document_record(
            db,
            driver=driver,
            document_type=DocumentType.CDL,
            google_drive_file_id="demo-cdl",
            google_drive_url="https://drive.google.com/",
            original_filename="john_smith_cdl.pdf",
        )
        db.commit()
        print("Demo data created")


if __name__ == "__main__":
    main()
