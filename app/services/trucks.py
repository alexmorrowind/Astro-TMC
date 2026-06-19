from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import Company, DriverStatus, EntityType, Truck, TruckDocument
from app.services.drivers import get_or_create_company, list_required_document_names


def upsert_truck(
    db: Session,
    *,
    company_name: str,
    unit_number: str,
    year: str | None = None,
    make: str | None = None,
    vin: str | None = None,
    license_plate: str | None = None,
    drive_folder_id: str | None = None,
    drive_folder_url: str | None = None,
) -> Truck:
    company = get_or_create_company(db, company_name)
    truck = db.scalar(
        select(Truck).where(
            Truck.company_id == company.id,
            Truck.unit_number == unit_number,
        )
    )
    if truck is None:
        truck = Truck(company_id=company.id, unit_number=unit_number)
        db.add(truck)

    truck.company = company
    truck.unit_number = unit_number
    truck.year = year or truck.year
    truck.make = make or truck.make
    truck.vin = vin or truck.vin
    truck.license_plate = license_plate or truck.license_plate
    truck.drive_folder_id = drive_folder_id or truck.drive_folder_id
    truck.drive_folder_url = drive_folder_url or truck.drive_folder_url
    db.flush()
    return truck


def add_truck_document_record(
    db: Session,
    *,
    truck: Truck,
    document_name: str,
    google_drive_file_id: str,
    google_drive_url: str,
    original_filename: str | None,
    local_file_path: str | None = None,
    mime_type: str | None = None,
) -> TruckDocument:
    existing = db.scalar(
        select(TruckDocument).where(
            TruckDocument.truck_id == truck.id,
            TruckDocument.document_name == document_name,
        )
    )
    if existing:
        existing.google_drive_file_id = google_drive_file_id
        existing.google_drive_url = google_drive_url
        existing.original_filename = original_filename
        existing.local_file_path = local_file_path
        existing.mime_type = mime_type
        document = existing
    else:
        document = TruckDocument(
            truck_id=truck.id,
            document_name=document_name,
            google_drive_file_id=google_drive_file_id,
            google_drive_url=google_drive_url,
            local_file_path=local_file_path,
            mime_type=mime_type,
            original_filename=original_filename,
        )
        db.add(document)
    db.flush()
    refresh_truck_status_from_db(db, truck)
    return document


def list_trucks_with_documents(db: Session, company_id: int | None = None) -> list[Truck]:
    stmt = (
        select(Truck)
        .options(joinedload(Truck.company), joinedload(Truck.documents))
        .order_by(Truck.created_at.desc())
    )
    if company_id:
        stmt = stmt.where(Truck.company_id == company_id)
    return list(db.scalars(stmt).unique())


def refresh_truck_status_from_db(db: Session, truck: Truck) -> None:
    required_names = list_required_document_names(db, company_id=truck.company_id, entity_type=EntityType.TRUCK)
    uploaded_names = {
        row[0]
        for row in db.execute(
            select(TruckDocument.document_name).where(TruckDocument.truck_id == truck.id)
        )
    }
    if all(name in uploaded_names for name in required_names):
        truck.status = DriverStatus.ACTIVE
    elif uploaded_names:
        truck.status = DriverStatus.MISSING_DOCS
    else:
        truck.status = DriverStatus.PENDING
    db.flush()
