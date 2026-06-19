from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Company, TelegramGroup
from app.services.drivers import get_or_create_company


def upsert_telegram_group(
    db: Session,
    *,
    chat_id: int,
    title: str,
    chat_type: str,
    company_name: str | None,
    linked_by_telegram_id: int | None,
    linked_by_username: str | None,
) -> TelegramGroup:
    company: Company | None = None
    if company_name:
        company = get_or_create_company(db, company_name)

    group = db.scalar(select(TelegramGroup).where(TelegramGroup.chat_id == chat_id))
    if group is None:
        group = TelegramGroup(chat_id=chat_id, title=title, chat_type=chat_type)
        db.add(group)

    group.title = title
    group.chat_type = chat_type
    group.company = company
    group.linked_by_telegram_id = linked_by_telegram_id
    group.linked_by_username = linked_by_username
    group.is_active = True
    db.flush()
    return group


def upsert_discovered_telegram_group(
    db: Session,
    *,
    chat_id: int,
    title: str,
    chat_type: str,
    default_active: bool,
) -> TelegramGroup:
    group = db.scalar(select(TelegramGroup).where(TelegramGroup.chat_id == chat_id))
    if group is None:
        group = TelegramGroup(
            chat_id=chat_id,
            title=title,
            chat_type=chat_type,
            is_active=default_active,
        )
        db.add(group)
    else:
        group.title = title
        group.chat_type = chat_type
    db.flush()
    return group


def list_groups_for_company(db: Session, company_id: int | None = None) -> list[TelegramGroup]:
    stmt = select(TelegramGroup).order_by(TelegramGroup.updated_at.desc())
    if company_id:
        stmt = stmt.where(TelegramGroup.company_id == company_id)
    return list(db.scalars(stmt))


def touch_group_activity(
    db: Session,
    *,
    chat_id: int,
    title: str,
    chat_type: str,
    from_username: str | None,
) -> TelegramGroup | None:
    group = db.scalar(select(TelegramGroup).where(TelegramGroup.chat_id == chat_id))
    if group is None or not group.is_active:
        return None

    group.title = title
    group.chat_type = chat_type
    group.last_seen_at = datetime.utcnow()
    group.last_message_from = from_username
    group.message_count = (group.message_count or 0) + 1
    db.flush()
    return group


def touch_or_create_group_activity(
    db: Session,
    *,
    chat_id: int,
    title: str,
    chat_type: str,
    from_username: str | None,
    default_active: bool,
) -> TelegramGroup:
    group = db.scalar(select(TelegramGroup).where(TelegramGroup.chat_id == chat_id))
    if group is None:
        group = TelegramGroup(
            chat_id=chat_id,
            title=title,
            chat_type=chat_type,
            is_active=default_active,
        )
        db.add(group)
    group.title = title
    group.chat_type = chat_type
    group.last_seen_at = datetime.utcnow()
    group.last_message_from = from_username
    group.message_count = (group.message_count or 0) + 1
    db.flush()
    return group
