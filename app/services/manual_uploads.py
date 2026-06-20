import mimetypes
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models import Driver, Truck
from app.services.drive import GoogleDriveStorage
from app.services.drive_sync import document_type_for_name, guess_document_name
from app.services.drivers import add_document_record, canonical_document_name
from app.services.incoming import safe_filename
from app.services.trucks import add_truck_document_record


MANUAL_UPLOAD_DIR = Path("uploads/manual")


@dataclass(frozen=True)
class StoredUpload:
    local_path: str
    original_filename: str
    mime_type: str | None


def store_uploaded_file(*, source_path: str | Path, original_filename: str, mime_type: str | None) -> StoredUpload:
    source = Path(source_path)
    MANUAL_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    extension = Path(original_filename).suffix or mimetypes.guess_extension(mime_type or "") or ".bin"
    stored_name = f"{uuid4().hex}_{safe_filename(Path(original_filename).stem)}{extension}"
    destination = MANUAL_UPLOAD_DIR / stored_name
    shutil.copyfile(source, destination)
    return StoredUpload(
        local_path=str(destination),
        original_filename=original_filename,
        mime_type=mime_type,
    )


def guess_uploaded_document_name(
    *,
    file_name: str,
    required_names: list[str],
    fallback_name: str | None = None,
) -> str:
    guessed = guess_document_name(file_name, required_names)
    return canonical_document_name(guessed or fallback_name or "Other")


def upload_driver_document_from_site(
    db: Session,
    *,
    driver: Driver,
    stored: StoredUpload,
    document_name: str,
    expiration_date: date | None,
) -> None:
    final_document_name = canonical_document_name(document_name)
    uploaded = GoogleDriveStorage().upload_driver_document(
        company_name=driver.company.name,
        driver_name=driver.full_name,
        document_type=final_document_name,
        source_path=stored.local_path,
    )
    if uploaded.folder_id:
        driver.drive_folder_id = uploaded.folder_id
    if uploaded.folder_url:
        driver.drive_folder_url = uploaded.folder_url
    add_document_record(
        db,
        driver=driver,
        document_type=document_type_for_name(final_document_name),
        document_name=final_document_name,
        google_drive_file_id=uploaded.file_id,
        google_drive_url=uploaded.web_view_link,
        original_filename=stored.original_filename,
        local_file_path=stored.local_path,
        mime_type=stored.mime_type,
        expiration_date=expiration_date,
    )


def upload_truck_document_from_site(
    db: Session,
    *,
    truck: Truck,
    stored: StoredUpload,
    document_name: str,
    expiration_date: date | None,
) -> None:
    final_document_name = canonical_document_name(document_name)
    uploaded = GoogleDriveStorage().upload_truck_document(
        company_name=truck.company.name,
        truck_name=truck.unit_number,
        document_type=final_document_name,
        source_path=stored.local_path,
    )
    if uploaded.folder_id:
        truck.drive_folder_id = uploaded.folder_id
    if uploaded.folder_url:
        truck.drive_folder_url = uploaded.folder_url
    document = add_truck_document_record(
        db,
        truck=truck,
        document_name=final_document_name,
        google_drive_file_id=uploaded.file_id,
        google_drive_url=uploaded.web_view_link,
        original_filename=stored.original_filename,
        local_file_path=stored.local_path,
        mime_type=stored.mime_type,
    )
    document.expiration_date = expiration_date
