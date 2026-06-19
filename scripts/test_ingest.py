import argparse
import sys
from pathlib import Path

import httpx

sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Send a local file to the Telegram ingest endpoint")
    parser.add_argument("file")
    parser.add_argument("--chat-id", type=int, default=-1001234567890)
    parser.add_argument("--chat-title", default="Collector Test Group")
    parser.add_argument("--url", default="http://127.0.0.1:8001")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"File not found: {path}")

    settings = get_settings()
    headers = {}
    if settings.telegram_ingest_token:
        headers["X-Ingest-Token"] = settings.telegram_ingest_token

    with path.open("rb") as handle:
        response = httpx.post(
            f"{args.url.rstrip('/')}/api/telegram/documents/ingest",
            data={
                "chat_id": str(args.chat_id),
                "chat_title": args.chat_title,
                "message_id": "1",
                "sender_id": "42",
                "sender_username": "local_test",
                "sender_name": "Local Test",
                "caption": "Local ingest smoke test",
            },
            files={"file": (path.name, handle, "application/octet-stream")},
            headers=headers,
            timeout=30,
        )
    response.raise_for_status()
    print(response.json())


if __name__ == "__main__":
    main()
