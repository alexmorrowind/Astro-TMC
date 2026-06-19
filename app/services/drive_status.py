from app.models import Driver, Truck
from app.services.drive import GoogleDriveStorage


def move_entity_folder_to_status(entity: Driver | Truck, status_name: str) -> bool:
    if not entity.drive_folder_id:
        return False

    drive = GoogleDriveStorage()
    if not drive.enabled:
        return False

    parents = drive.get_file_parents(entity.drive_folder_id)
    if not parents:
        return False

    current_parent_id = parents[0]
    current_parent = drive.service().files().get(
        fileId=current_parent_id,
        fields="id, name, parents",
        supportsAllDrives=True,
    ).execute()
    parent_name = (current_parent.get("name") or "").strip().lower()
    if parent_name in {"active", "inactive", "1inactive", "not active"}:
        company_folder_id = (current_parent.get("parents") or [None])[0]
    else:
        company_folder_id = current_parent_id

    if not company_folder_id:
        return False

    drive.move_folder_to_status_container(
        folder_id=entity.drive_folder_id,
        company_folder_id=company_folder_id,
        status_name=status_name,
    )
    return True
