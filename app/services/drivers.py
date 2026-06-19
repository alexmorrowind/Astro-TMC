from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.models import Company, Document, DocumentType, Driver, DriverStatus, EntityType, RequiredDocument


REQUIRED_DOCUMENTS = [DocumentType.CDL, DocumentType.MEDICAL_CARD]
DEFAULT_DRIVER_DOCUMENT_NAMES = [
    "CDL",
    "Medical Examination Certificate (Medical Card)",
    "Social Security number (SSN)",
    "Work Authorization / Green Card / US Passport",
    "Email & Phone# - Emergency contact",
    "DrugTest",
    "CCF form",
    "Clearinghouse",
    "Contract",
]
DEFAULT_TRUCK_DOCUMENT_NAMES = [
    "Truck Registration",
    "Lease Agreement",
    "Annual Inspection",
    "OR Permit",
    "NY Permit",
    "NM Permit",
    "KYU Permit",
]
DEFAULT_REQUIRED_DOCUMENT_NAMES = DEFAULT_DRIVER_DOCUMENT_NAMES
DOCUMENT_NAME_ALIASES = {
    "Medical Card": "Medical Examination Certificate (Medical Card)",
    "Med Card": "Medical Examination Certificate (Medical Card)",
    "SSN": "Social Security number (SSN)",
    "Drug Test Results": "DrugTest",
    "Driver Agreement": "Contract",
}


def canonical_document_name(name: str) -> str:
    return DOCUMENT_NAME_ALIASES.get(name, name)


def get_or_create_company(db: Session, name: str) -> Company:
    clean_name = " ".join(name.strip().split())
    company = db.scalar(select(Company).where(Company.name == clean_name))
    if company:
        return company
    company = Company(name=clean_name)
    db.add(company)
    db.flush()
    return company


def upsert_driver(
    db: Session,
    *,
    company_name: str,
    telegram_id: int | None,
    telegram_username: str | None,
    full_name: str,
    birth_date: date | None,
    phone: str | None,
    license_number: str | None,
) -> Driver:
    company = get_or_create_company(db, company_name)
    driver = None
    if telegram_id is not None:
        driver = db.scalar(select(Driver).where(Driver.telegram_id == telegram_id))
    if driver is None:
        driver = db.scalar(
            select(Driver).where(Driver.company_id == company.id, Driver.full_name == full_name)
        )

    if driver is None:
        driver = Driver(company_id=company.id, full_name=full_name)
        db.add(driver)

    driver.company = company
    driver.telegram_id = telegram_id
    driver.telegram_username = telegram_username
    driver.full_name = full_name
    driver.birth_date = birth_date
    driver.phone = phone
    driver.license_number = license_number
    driver.status = DriverStatus.PENDING
    db.flush()
    return driver


def add_document_record(
    db: Session,
    *,
    driver: Driver,
    document_type: DocumentType,
    document_name: str | None = None,
    google_drive_file_id: str,
    google_drive_url: str,
    original_filename: str | None,
    local_file_path: str | None = None,
    mime_type: str | None = None,
    expiration_date: date | None = None,
) -> Document:
    final_document_name = canonical_document_name(document_name or document_type.value)
    existing = db.scalar(
        select(Document).where(
            Document.driver_id == driver.id,
            Document.document_name == final_document_name,
        )
    )
    if existing:
        existing.google_drive_file_id = google_drive_file_id
        existing.google_drive_url = google_drive_url
        existing.document_type = document_type
        existing.document_name = final_document_name
        existing.original_filename = original_filename
        existing.local_file_path = local_file_path
        existing.mime_type = mime_type
        existing.expiration_date = expiration_date
        document = existing
    else:
        document = Document(
            driver_id=driver.id,
            document_type=document_type,
            document_name=final_document_name,
            google_drive_file_id=google_drive_file_id,
            google_drive_url=google_drive_url,
            local_file_path=local_file_path,
            mime_type=mime_type,
            original_filename=original_filename,
            expiration_date=expiration_date,
        )
        db.add(document)
    db.flush()
    refresh_driver_status_from_db(db, driver)
    return document


def refresh_driver_status(driver: Driver) -> None:
    required_names = DEFAULT_REQUIRED_DOCUMENT_NAMES
    uploaded_names = {canonical_document_name(document.display_name) for document in driver.documents}
    if all(document_name in uploaded_names for document_name in required_names):
        driver.status = DriverStatus.ACTIVE
    elif uploaded_names:
        driver.status = DriverStatus.MISSING_DOCS
    else:
        driver.status = DriverStatus.PENDING


def list_drivers_with_documents(db: Session, company_id: int | None = None) -> list[Driver]:
    stmt = (
        select(Driver)
        .options(joinedload(Driver.company), joinedload(Driver.documents))
        .order_by(Driver.created_at.desc())
    )
    if company_id:
        stmt = stmt.where(Driver.company_id == company_id)
    return list(db.scalars(stmt).unique())


def sync_required_document_group(db: Session, entity_type: EntityType, names: list[str]) -> None:
    for index, name in enumerate(names):
        existing = db.scalar(
            select(RequiredDocument).where(
                RequiredDocument.company_id.is_(None),
                RequiredDocument.entity_type == entity_type,
                RequiredDocument.name == name,
            )
        )
        if existing:
            existing.is_active = True
            existing.sort_order = index
        else:
            db.add(RequiredDocument(entity_type=entity_type, name=name, sort_order=index, is_active=True))
    db.flush()


def sync_default_required_documents(db: Session) -> None:
    sync_required_document_group(db, EntityType.DRIVER, DEFAULT_DRIVER_DOCUMENT_NAMES)
    sync_required_document_group(db, EntityType.TRUCK, DEFAULT_TRUCK_DOCUMENT_NAMES)


def list_required_document_names(
    db: Session,
    company_id: int | None = None,
    entity_type: EntityType = EntityType.DRIVER,
) -> list[str]:
    stmt = (
        select(RequiredDocument)
        .where(RequiredDocument.is_active == True)
        .where(RequiredDocument.entity_type == entity_type)
        .order_by(RequiredDocument.sort_order, RequiredDocument.name)
    )
    if company_id:
        stmt = stmt.where(
            (RequiredDocument.company_id == company_id)
            | (RequiredDocument.company_id.is_(None))
        )
    else:
        stmt = stmt.where(RequiredDocument.company_id.is_(None))

    names = []
    seen = set()
    for document in db.scalars(stmt):
        canonical_name = canonical_document_name(document.name)
        if canonical_name not in seen:
            names.append(canonical_name)
            seen.add(canonical_name)
    if names:
        return names
    return DEFAULT_TRUCK_DOCUMENT_NAMES if entity_type == EntityType.TRUCK else DEFAULT_DRIVER_DOCUMENT_NAMES


def refresh_driver_status_from_db(db: Session, driver: Driver) -> None:
    required_names = list_required_document_names(db, company_id=driver.company_id)
    uploaded_names = {
        canonical_document_name(row[0])
        for row in db.execute(
            select(Document.document_name).where(
                Document.driver_id == driver.id,
                Document.document_name.is_not(None),
            )
        )
    }
    fallback_types = {
        row[0].value if hasattr(row[0], "value") else str(row[0])
        for row in db.execute(
            select(Document.document_type).where(Document.driver_id == driver.id)
        )
    }
    uploaded_names |= fallback_types
    if all(name in uploaded_names for name in required_names):
        driver.status = DriverStatus.ACTIVE
    elif uploaded_names:
        driver.status = DriverStatus.MISSING_DOCS
    else:
        driver.status = DriverStatus.PENDING
    db.flush()
