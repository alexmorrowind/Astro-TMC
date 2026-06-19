import asyncio

from telethon import TelegramClient

from app.config import get_settings


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
    await client.start(phone=settings.telegram_collector_phone)
    print("Available dialogs for collector account:")
    async for dialog in client.iter_dialogs():
        entity = dialog.entity
        if dialog.is_group or dialog.is_channel or dialog.is_user:
            if dialog.is_user:
                chat_type = "private"
            else:
                chat_type = "channel" if dialog.is_channel and not getattr(entity, "megagroup", False) else "group"
            print(f"{dialog.id}\t{chat_type}\t{dialog.name}")
    await client.disconnect()


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
