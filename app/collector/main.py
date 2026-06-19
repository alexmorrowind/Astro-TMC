import asyncio
import mimetypes
import tempfile
from pathlib import Path
from datetime import datetime

import httpx
from sqlalchemy import select
from telethon import TelegramClient, events
from telethon.errors import PasswordHashInvalidError

from app.config import get_settings
from app.database import SessionLocal
from app.models import OutboxStatus, TelegramGroup
from app.services.groups import touch_group_activity, upsert_discovered_telegram_group
from app.services.outbox import pending_outbox_messages


def collector_backend_url() -> str:
    settings = get_settings()
    return (settings.telegram_collector_backend_url or settings.public_base_url).rstrip("/")


def is_supported_media(event) -> bool:
    message = event.message
    if not message:
        return False
    if message.document:
        return True
    if message.photo:
        return True
    return False


def guess_file_name(event) -> str:
    message = event.message
    if message.file and message.file.name:
        return message.file.name
    if message.file and message.file.mime_type:
        extension = mimetypes.guess_extension(message.file.mime_type) or ".bin"
    elif message.photo:
        extension = ".jpg"
    else:
        extension = ".bin"
    return f"telegram_{message.id}{extension}"


def should_accept_chat(chat_id: int) -> bool:
    settings = get_settings()
    if not settings.telegram_collector_require_connected_groups:
        return True
    with SessionLocal() as db:
        group = db.scalar(
            select(TelegramGroup).where(
                TelegramGroup.chat_id == chat_id,
                TelegramGroup.is_active == True,
            )
        )
        return group is not None


def dialog_chat_type(dialog) -> str:
    entity = dialog.entity
    if dialog.is_user:
        return "private"
    if dialog.is_channel and not getattr(entity, "megagroup", False):
        return "channel"
    if getattr(entity, "megagroup", False):
        return "supergroup"
    return "group"


def event_chat_type(chat) -> str:
    if getattr(chat, "broadcast", False):
        return "channel"
    if getattr(chat, "megagroup", False):
        return "supergroup"
    if getattr(chat, "title", None):
        return "group"
    return "private"


def ingest_headers() -> dict[str, str]:
    settings = get_settings()
    headers = {}
    if settings.telegram_ingest_token:
        headers["X-Ingest-Token"] = settings.telegram_ingest_token
    return headers


async def post_json_to_backend(path: str, payload: dict) -> dict:
    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{collector_backend_url()}{path}",
            json=payload,
            headers=ingest_headers(),
        )
    response.raise_for_status()
    return response.json()


async def sync_dialogs(client: TelegramClient) -> None:
    settings = get_settings()
    discovered = 0
    default_active = not settings.telegram_collector_require_connected_groups
    remote_chats = []
    with SessionLocal() as db:
        async for dialog in client.iter_dialogs():
            if not (dialog.is_group or dialog.is_channel or dialog.is_user):
                continue
            chat_type = dialog_chat_type(dialog)
            title = dialog.name or str(dialog.id)
            upsert_discovered_telegram_group(
                db,
                chat_id=dialog.id,
                title=title,
                chat_type=chat_type,
                default_active=default_active,
            )
            remote_chats.append({"chat_id": dialog.id, "title": title, "chat_type": chat_type})
            discovered += 1
        db.commit()
    try:
        result = await post_json_to_backend(
            "/api/telegram/chats/sync",
            {"default_active": default_active, "chats": remote_chats},
        )
        remote_synced = result.get("synced", 0)
    except Exception as exc:
        print(f"Remote Telegram chat sync failed: {exc}")
        remote_synced = 0
    print(f"Synced {discovered} Telegram chats/groups/channels into local CRM and {remote_synced} into backend.")


async def sync_dialogs_loop(client: TelegramClient) -> None:
    while True:
        await asyncio.sleep(60)
        try:
            await sync_dialogs(client)
        except Exception as exc:
            print(f"Telegram dialog sync failed: {exc}")


async def send_to_backend(
    *,
    file_path: Path,
    file_name: str,
    chat_id: int,
    chat_title: str | None,
    message_id: int,
    sender_id: int | None,
    sender_username: str | None,
    sender_name: str | None,
    caption: str | None,
    mime_type: str | None,
) -> dict:
    data = {
        "chat_id": str(chat_id),
        "chat_title": chat_title or "",
        "message_id": str(message_id),
        "sender_id": str(sender_id or ""),
        "sender_username": sender_username or "",
        "sender_name": sender_name or "",
        "caption": caption or "",
    }
    with file_path.open("rb") as handle:
        files = {"file": (file_name, handle, mime_type or "application/octet-stream")}
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{collector_backend_url()}/api/telegram/documents/ingest",
                data=data,
                files=files,
                headers=ingest_headers(),
            )
    response.raise_for_status()
    return response.json()


async def handle_new_message(event) -> None:
    if not is_supported_media(event):
        return

    chat = await event.get_chat()
    sender = await event.get_sender()
    chat_id = event.chat_id
    if chat_id is None or not should_accept_chat(chat_id):
        return

    sender_name = " ".join(
        part for part in [getattr(sender, "first_name", None), getattr(sender, "last_name", None)] if part
    ) or None
    from_username = getattr(sender, "username", None) or sender_name or str(getattr(sender, "id", ""))
    chat_title = getattr(chat, "title", None) or sender_name or getattr(chat, "username", None) or str(chat_id)
    chat_type = event_chat_type(chat)
    with SessionLocal() as db:
        upsert_discovered_telegram_group(
            db,
            chat_id=chat_id,
            title=chat_title,
            chat_type=chat_type,
            default_active=not get_settings().telegram_collector_require_connected_groups,
        )
        touch_group_activity(
            db,
            chat_id=chat_id,
            title=chat_title,
            chat_type=chat_type,
            from_username=from_username,
        )
        db.commit()
    try:
        await post_json_to_backend(
            "/api/telegram/chats/activity",
            {
                "chat_id": chat_id,
                "title": chat_title,
                "chat_type": chat_type,
                "from_username": from_username,
            },
        )
    except Exception as exc:
        print(f"Remote Telegram chat activity sync failed for {chat_id}: {exc}")

    file_name = guess_file_name(event)
    mime_type = event.message.file.mime_type if event.message.file else None
    with tempfile.TemporaryDirectory() as temp_dir:
        destination = Path(temp_dir) / file_name
        downloaded = await event.message.download_media(file=destination)
        if not downloaded:
            return

        result = await send_to_backend(
            file_path=Path(downloaded),
            file_name=file_name,
            chat_id=chat_id,
            chat_title=chat_title,
            message_id=event.message.id,
            sender_id=getattr(sender, "id", None),
            sender_username=getattr(sender, "username", None),
            sender_name=sender_name,
            caption=event.message.message,
            mime_type=mime_type,
        )
    print(f"Stored incoming Telegram document #{result['id']} from chat {chat_id}: {file_name}")


async def send_outbox_message(client: TelegramClient, message) -> None:
    if message.photo_path and Path(message.photo_path).exists():
        await client.send_file(message.chat_id, message.photo_path, caption=message.text)
    else:
        await client.send_message(message.chat_id, message.text)


async def process_outbox_loop(client: TelegramClient) -> None:
    while True:
        with SessionLocal() as db:
            messages = pending_outbox_messages(db)
            for message in messages:
                try:
                    await send_outbox_message(client, message)
                except Exception as exc:
                    message.status = OutboxStatus.FAILED
                    message.error = str(exc)
                else:
                    message.status = OutboxStatus.SENT
                    message.sent_at = datetime.utcnow()
                    message.error = None
            db.commit()
        await asyncio.sleep(5)


async def main_async() -> None:
    settings = get_settings()
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        raise RuntimeError("TELEGRAM_API_ID and TELEGRAM_API_HASH are required")
    if not settings.telegram_collector_phone:
        raise RuntimeError("TELEGRAM_COLLECTOR_PHONE is required")

    client = TelegramClient(
        settings.telegram_collector_session,
        int(settings.telegram_api_id),
        settings.telegram_api_hash,
    )
    try:
        await client.start(
            phone=settings.telegram_collector_phone,
            password=lambda: input(
                "Enter Telegram Two-Step Verification password "
                "(not your Mac/Google password): "
            ),
        )
    except PasswordHashInvalidError as exc:
        raise SystemExit(
            "Telegram rejected the Two-Step Verification password. "
            "Open Telegram -> Settings -> Privacy and Security -> Two-Step Verification "
            "and use that cloud password, or reset it in Telegram."
        ) from exc
    await sync_dialogs(client)
    client.add_event_handler(handle_new_message, events.NewMessage())
    asyncio.create_task(sync_dialogs_loop(client))
    asyncio.create_task(process_outbox_loop(client))
    print("Telegram collector is running. Press Ctrl+C to stop.")
    await client.run_until_disconnected()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
