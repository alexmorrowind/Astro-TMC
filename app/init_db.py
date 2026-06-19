from sqlalchemy import inspect, select, text

from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.models import Document, EntityType, RequiredDocument, User, UserRole
from app.security import hash_password
from app.services.drivers import DEFAULT_DRIVER_DOCUMENT_NAMES, DEFAULT_TRUCK_DOCUMENT_NAMES, sync_default_required_documents


REQUIRED_DOCUMENT_RENAMES = {
    "Medical Card": "Medical Examination Certificate (Medical Card)",
    "Med Card": "Medical Examination Certificate (Medical Card)",
    "SSN": "Social Security number (SSN)",
    "Drug Test Results": "DrugTest",
    "Driver Agreement": "Contract",
}


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    apply_sqlite_migrations()
    settings = get_settings()

    with SessionLocal() as db:
        sync_default_required_documents(db)
        backfill_document_names(db)

        existing_admin = db.scalar(
            select(User).where(User.username == settings.superadmin_username)
        )
        if existing_admin:
            db.commit()
            return

        admin = User(
            username=settings.superadmin_username,
            password_hash=hash_password(settings.superadmin_password),
            role=UserRole.SUPERADMIN,
        )
        db.add(admin)
        db.commit()


def apply_sqlite_migrations() -> None:
    if not get_settings().database_url.startswith("sqlite"):
        return

    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    if "telegram_groups" not in table_names:
        telegram_group_statements = []
    else:
        existing_columns = {column["name"] for column in inspector.get_columns("telegram_groups")}
        telegram_group_statements = []
        if "last_seen_at" not in existing_columns:
            telegram_group_statements.append("ALTER TABLE telegram_groups ADD COLUMN last_seen_at DATETIME")
        if "last_message_from" not in existing_columns:
            telegram_group_statements.append("ALTER TABLE telegram_groups ADD COLUMN last_message_from VARCHAR(255)")
        if "message_count" not in existing_columns:
            telegram_group_statements.append("ALTER TABLE telegram_groups ADD COLUMN message_count INTEGER DEFAULT 0")
        if "driver_id" not in existing_columns:
            telegram_group_statements.append("ALTER TABLE telegram_groups ADD COLUMN driver_id INTEGER")

    document_statements = []
    if "documents" in table_names:
        document_columns = {column["name"] for column in inspector.get_columns("documents")}
        if "local_file_path" not in document_columns:
            document_statements.append("ALTER TABLE documents ADD COLUMN local_file_path TEXT")
        if "mime_type" not in document_columns:
            document_statements.append("ALTER TABLE documents ADD COLUMN mime_type VARCHAR(255)")
        if "document_name" not in document_columns:
            document_statements.append("ALTER TABLE documents ADD COLUMN document_name VARCHAR(255)")

    driver_statements = []
    if "drivers" in table_names:
        driver_columns = {column["name"] for column in inspector.get_columns("drivers")}
        if "address" not in driver_columns:
            driver_statements.append("ALTER TABLE drivers ADD COLUMN address TEXT")
        if "email" not in driver_columns:
            driver_statements.append("ALTER TABLE drivers ADD COLUMN email VARCHAR(255)")
        if "emergency_contact" not in driver_columns:
            driver_statements.append("ALTER TABLE drivers ADD COLUMN emergency_contact VARCHAR(255)")
        if "group_inactive" not in driver_columns:
            driver_statements.append("ALTER TABLE drivers ADD COLUMN group_inactive BOOLEAN DEFAULT 0")
        if "terminated_at" not in driver_columns:
            driver_statements.append("ALTER TABLE drivers ADD COLUMN terminated_at DATETIME")

    truck_statements = []
    if "trucks" in table_names:
        truck_columns = {column["name"] for column in inspector.get_columns("trucks")}
        if "terminated_at" not in truck_columns:
            truck_statements.append("ALTER TABLE trucks ADD COLUMN terminated_at DATETIME")

    required_document_statements = []
    if "required_documents" in table_names:
        required_document_columns = {column["name"] for column in inspector.get_columns("required_documents")}
        if "entity_type" not in required_document_columns:
            required_document_statements.append(
                "ALTER TABLE required_documents ADD COLUMN entity_type VARCHAR(6) DEFAULT 'DRIVER'"
            )

    company_statements = []
    if "companies" in table_names:
        company_columns = {column["name"] for column in inspector.get_columns("companies")}
        if "is_hidden" not in company_columns:
            company_statements.append("ALTER TABLE companies ADD COLUMN is_hidden BOOLEAN DEFAULT 0")
        if "terminated_at" not in company_columns:
            company_statements.append("ALTER TABLE companies ADD COLUMN terminated_at DATETIME")

    with engine.begin() as connection:
        for statement in (
            telegram_group_statements
            + document_statements
            + driver_statements
            + truck_statements
            + required_document_statements
            + company_statements
        ):
            connection.execute(text(statement))


def backfill_document_names(db) -> None:
    for document in db.scalars(select(Document).where(Document.document_name.is_(None))):
        document.document_name = document.document_type.value
    for required_document in db.scalars(select(RequiredDocument)):
        if required_document.entity_type is None:
            required_document.entity_type = EntityType.DRIVER
        replacement = REQUIRED_DOCUMENT_RENAMES.get(required_document.name)
        if replacement:
            duplicate = db.scalar(
                select(RequiredDocument).where(
                    RequiredDocument.id != required_document.id,
                    RequiredDocument.company_id == required_document.company_id,
                    RequiredDocument.entity_type == required_document.entity_type,
                    RequiredDocument.name == replacement,
                )
            )
            if duplicate:
                required_document.is_active = False
            else:
                required_document.name = replacement
    for index, name in enumerate(DEFAULT_DRIVER_DOCUMENT_NAMES):
        existing = db.scalar(
            select(RequiredDocument).where(
                RequiredDocument.company_id.is_(None),
                RequiredDocument.entity_type == EntityType.DRIVER,
                RequiredDocument.name == name,
            )
        )
        if existing:
            existing.sort_order = index
    for index, name in enumerate(DEFAULT_TRUCK_DOCUMENT_NAMES):
        existing = db.scalar(
            select(RequiredDocument).where(
                RequiredDocument.company_id.is_(None),
                RequiredDocument.entity_type == EntityType.TRUCK,
                RequiredDocument.name == name,
            )
        )
        if existing:
            existing.sort_order = index


if __name__ == "__main__":
    init_db()
