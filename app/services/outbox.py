import shutil
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import OutboxStatus, TelegramGroup, TelegramOutboxMessage, User
from app.services.incoming import safe_filename


OUTBOX_UPLOAD_DIR = Path("uploads/outbox")


def create_outbox_messages(
    db: Session,
    *,
    chats: list[TelegramGroup],
    text: str,
    photo_source_path: str | None,
    photo_filename: str | None,
    user: User,
) -> list[TelegramOutboxMessage]:
    stored_photo_path = None
    if photo_source_path:
        OUTBOX_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        source = Path(photo_source_path)
        suffix = Path(photo_filename or source.name).suffix or source.suffix or ".bin"
        stored_photo_path = OUTBOX_UPLOAD_DIR / f"{uuid4().hex}_{safe_filename(source.stem)}{suffix}"
        shutil.copyfile(source, stored_photo_path)

    messages = []
    for chat in chats:
        message = TelegramOutboxMessage(
            chat_id=chat.chat_id,
            text=text,
            photo_path=str(stored_photo_path) if stored_photo_path else None,
            created_by_user_id=user.id,
        )
        db.add(message)
        messages.append(message)
    db.flush()
    return messages


def pending_outbox_messages(db: Session, limit: int = 20) -> list[TelegramOutboxMessage]:
    return list(
        db.scalars(
            select(TelegramOutboxMessage)
            .where(TelegramOutboxMessage.status == OutboxStatus.PENDING)
            .order_by(TelegramOutboxMessage.created_at)
            .limit(limit)
        )
    )
