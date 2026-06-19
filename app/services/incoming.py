import mimetypes
import re
import shutil
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    DocumentType,
    Driver,
    IncomingDocumentStatus,
    IncomingTelegramDocument,
    TelegramGroup,
)
from app.services.drive import GoogleDriveStorage
from app.services.drivers import add_document_record, canonical_document_name


INCOMING_UPLOAD_DIR = Path("uploads/incoming_telegram")


@dataclass(frozen=True)
class IncomingTelegramMetadata:
    chat_id: int
    chat_title: str | None = None
    message_id: int | None = None
    sender_id: int | None = None
    sender_username: str | None = None
    sender_name: str | None = None
    caption: str | None = None
    mime_type: str | None = None
    file_name: str | None = None


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", " ", value)
    cleaned = re.sub(r"\s+", "_", cleaned.strip())
    return cleaned or "telegram_document"


def store_incoming_document(
    db: Session,
    *,
    source_path: str | Path,
    metadata: IncomingTelegramMetadata,
) -> IncomingTelegramDocument:
    source = Path(source_path)
    INCOMING_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    original_name = metadata.file_name or source.name
    extension = Path(original_name).suffix or mimetypes.guess_extension(metadata.mime_type or "") or ".bin"
    stored_name = f"{uuid4().hex}_{safe_filename(Path(original_name).stem)}{extension}"
    destination = INCOMING_UPLOAD_DIR / stored_name
    shutil.copyfile(source, destination)

    group = db.scalar(
        select(TelegramGroup).where(
            TelegramGroup.chat_id == metadata.chat_id,
            TelegramGroup.is_active == True,
        )
    )

    incoming = IncomingTelegramDocument(
        company_id=group.company_id if group else None,
        telegram_chat_id=metadata.chat_id,
        telegram_chat_title=metadata.chat_title,
        telegram_message_id=metadata.message_id,
        sender_id=metadata.sender_id,
        sender_username=metadata.sender_username,
        sender_name=metadata.sender_name,
        file_name=original_name,
        mime_type=metadata.mime_type,
        local_file_path=str(destination),
        file_size=destination.stat().st_size,
        caption=metadata.caption,
    )
    db.add(incoming)
    db.flush()
    if group and group.driver:
        assign_incoming_document(
            db,
            incoming=incoming,
            driver=group.driver,
            document_name=guess_document_name(original_name, metadata.caption),
        )
    return incoming


def document_type_for_name(document_name: str) -> DocumentType:
    for document_type in DocumentType:
        if document_type.value.lower() == document_name.lower():
            return document_type
    return DocumentType.OTHER


def guess_document_name(file_name: str | None, caption: str | None) -> str:
    return canonical_document_name(guess_document_type(file_name, caption).value)


def guess_document_type(file_name: str | None, caption: str | None) -> DocumentType:
    text = f"{file_name or ''} {caption or ''}".lower()
    if "medical" in text or "med card" in text or "medcard" in text:
        return DocumentType.MEDICAL_CARD
    if "cdl" in text or "license" in text or "licence" in text:
        return DocumentType.CDL
    return DocumentType.OTHER


def assign_incoming_document(
    db: Session,
    *,
    incoming: IncomingTelegramDocument,
    driver: Driver,
    document_type: DocumentType | None = None,
    document_name: str | None = None,
    expiration_date: date | None = None,
) -> IncomingTelegramDocument:
    if incoming.status != IncomingDocumentStatus.NEW:
        raise ValueError("Incoming document is already processed")

    final_document_name = canonical_document_name(
        document_name or (document_type.value if document_type else DocumentType.OTHER.value)
    )
    final_document_type = document_type or document_type_for_name(final_document_name)
    uploaded = GoogleDriveStorage().upload_driver_document(
        company_name=driver.company.name,
        driver_name=driver.full_name,
        document_type=final_document_name,
        source_path=incoming.local_file_path,
    )
    if uploaded.folder_id:
        driver.drive_folder_id = uploaded.folder_id
    if uploaded.folder_url:
        driver.drive_folder_url = uploaded.folder_url

    document = add_document_record(
        db,
        driver=driver,
        document_type=final_document_type,
        document_name=final_document_name,
        google_drive_file_id=uploaded.file_id,
        google_drive_url=uploaded.web_view_link,
        original_filename=incoming.file_name,
        local_file_path=incoming.local_file_path,
        mime_type=incoming.mime_type,
        expiration_date=expiration_date,
    )

    incoming.status = IncomingDocumentStatus.ASSIGNED
    incoming.assigned_driver_id = driver.id
    incoming.assigned_document_id = document.id
    incoming.assigned_at = datetime.utcnow()
    db.flush()
    return incoming
