import json
import mimetypes
import os
import re
from dataclasses import dataclass
from pathlib import Path

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

from app.config import get_settings


DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive"]


@dataclass(frozen=True)
class UploadedDriveFile:
    file_id: str
    web_view_link: str
    folder_id: str | None = None
    folder_url: str | None = None


@dataclass(frozen=True)
class DriveItem:
    id: str
    name: str
    mime_type: str
    web_view_link: str | None = None
    modified_time: str | None = None
    size: str | None = None

    @property
    def is_folder(self) -> bool:
        return self.mime_type == "application/vnd.google-apps.folder"


def safe_drive_name(value: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|]+", " ", value)
    cleaned = re.sub(r"\s+", "_", cleaned.strip())
    return cleaned or "Unknown"


class GoogleDriveStorage:
    def __init__(self) -> None:
        self.settings = get_settings()
        self._service = None

    @property
    def enabled(self) -> bool:
        has_credentials = bool(
            self.settings.google_credentials_json
            or Path(self.settings.google_credentials_file).exists()
        )
        return bool(self.settings.google_drive_root_folder_id and has_credentials)

    def service(self):
        if self._service is not None:
            return self._service
        if self.settings.google_credentials_json:
            credentials_info = json.loads(self.settings.google_credentials_json)
            credentials = service_account.Credentials.from_service_account_info(
                credentials_info,
                scopes=DRIVE_SCOPES,
            )
        else:
            credentials = service_account.Credentials.from_service_account_file(
                self.settings.google_credentials_file,
                scopes=DRIVE_SCOPES,
            )
        self._service = build("drive", "v3", credentials=credentials)
        return self._service

    def upload_driver_document(
        self,
        *,
        company_name: str,
        driver_name: str,
        document_type: str,
        source_path: str,
    ) -> UploadedDriveFile:
        extension = Path(source_path).suffix or mimetypes.guess_extension(
            mimetypes.guess_type(source_path)[0] or ""
        ) or ".bin"
        drive_filename = f"{safe_drive_name(driver_name)}_{safe_drive_name(document_type)}{extension}"

        if not self.enabled:
            mock_id = f"local-{safe_drive_name(company_name)}-{safe_drive_name(driver_name)}-{safe_drive_name(document_type)}"
            return UploadedDriveFile(
                file_id=mock_id,
                web_view_link=f"file://{os.path.abspath(source_path)}",
                folder_id=None,
                folder_url=None,
            )

        company_folder_id = self._get_or_create_folder(
            name=company_name,
            parent_id=self.settings.google_drive_root_folder_id,
        )
        driver_folder_id = self._get_or_create_folder(
            name=driver_name,
            parent_id=company_folder_id,
        )

        media = MediaFileUpload(source_path, resumable=False)
        file_metadata = {
            "name": drive_filename,
            "parents": [driver_folder_id],
        }
        created = (
            self.service()
            .files()
            .create(
                body=file_metadata,
                media_body=media,
                fields="id, webViewLink",
                supportsAllDrives=True,
            )
            .execute()
        )

        if self.settings.google_drive_create_public_links:
            self.service().permissions().create(
                fileId=created["id"],
                body={"type": "anyone", "role": "reader"},
                supportsAllDrives=True,
            ).execute()

        return UploadedDriveFile(
            file_id=created["id"],
            web_view_link=created["webViewLink"],
            folder_id=driver_folder_id,
            folder_url=f"https://drive.google.com/drive/folders/{driver_folder_id}",
        )

    def download_file(self, *, file_id: str, destination_path: str | Path) -> tuple[str | None, str | None]:
        if not self.enabled:
            raise RuntimeError("Google Drive is not configured")

        metadata = (
            self.service()
            .files()
            .get(fileId=file_id, fields="name, mimeType", supportsAllDrives=True)
            .execute()
        )
        request = self.service().files().get_media(fileId=file_id, supportsAllDrives=True)
        destination = Path(destination_path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("wb") as handle:
            downloader = MediaIoBaseDownload(handle, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        return metadata.get("name"), metadata.get("mimeType")

    def list_children(self, *, parent_id: str | None = None) -> list[DriveItem]:
        if not self.enabled:
            return []

        folder_id = parent_id or self.settings.google_drive_root_folder_id
        query = f"'{folder_id}' in parents and trashed=false"
        items: list[DriveItem] = []
        page_token = None
        while True:
            response = (
                self.service()
                .files()
                .list(
                    q=query,
                    fields="nextPageToken, files(id, name, mimeType, webViewLink, modifiedTime, size)",
                    spaces="drive",
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                    pageSize=1000,
                    pageToken=page_token,
                )
                .execute()
            )
            for item in response.get("files", []):
                items.append(
                    DriveItem(
                        id=item["id"],
                        name=item["name"],
                        mime_type=item["mimeType"],
                        web_view_link=item.get("webViewLink"),
                        modified_time=item.get("modifiedTime"),
                        size=item.get("size"),
                    )
                )
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return items

    def _get_or_create_folder(self, *, name: str, parent_id: str) -> str:
        escaped_name = name.replace("'", "\\'")
        query = (
            "mimeType='application/vnd.google-apps.folder' "
            f"and name='{escaped_name}' "
            f"and '{parent_id}' in parents "
            "and trashed=false"
        )
        response = (
            self.service()
            .files()
            .list(
                q=query,
                fields="files(id, name)",
                spaces="drive",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        files = response.get("files", [])
        if files:
            return files[0]["id"]

        metadata = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
            "parents": [parent_id],
        }
        folder = (
            self.service()
            .files()
            .create(body=metadata, fields="id", supportsAllDrives=True)
            .execute()
        )
        return folder["id"]
