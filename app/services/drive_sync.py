import re
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import DocumentType, Driver, EntityType, Truck
from app.services.drive import DriveItem, GoogleDriveStorage
from app.services.drivers import (
    add_document_record,
    canonical_document_name,
    get_or_create_company,
    list_required_document_names,
    refresh_driver_status_from_db,
)
from app.services.trucks import add_truck_document_record, refresh_truck_status_from_db, upsert_truck


DRIVER_CONTAINER_NAMES = {"driver", "drivers", "driver docs", "driver documents"}
TRUCK_CONTAINER_NAMES = {"truck", "trucks", "truck docs", "truck documents", "units", "unit"}
STATUS_CONTAINER_NAMES = {"active", "inactive", "1inactive", "not active"}

DOCUMENT_ALIASES = {
    "CDL": ["cdl", "license", "licence", "driver license", "commercial"],
    "Medical Examination Certificate (Medical Card)": [
        "medical",
        "med card",
        "medcard",
        "medical card",
        "certificate",
    ],
    "Social Security number (SSN)": ["ssn", "social security"],
    "Work Authorization / Green Card / US Passport": [
        "work authorization",
        "green card",
        "passport",
        "employment authorization",
        "ead",
        "uscis",
    ],
    "Email & Phone# - Emergency contact": ["emergency", "contact", "phone", "email"],
    "DrugTest": ["drug", "test", "random", "drugtest"],
    "CCF form": ["ccf"],
    "Clearinghouse": ["clearinghouse", "clearing house"],
    "Contract": ["agreement", "contract", "driver agreement"],
    "Truck Registration": ["registration", "reg"],
    "Lease Agreement": ["lease"],
    "Annual Inspection": ["annual inspection", "inspection"],
    "OR Permit": ["or permit", "oregon"],
    "NY Permit": ["ny permit", "new york", "hut"],
    "NM Permit": ["nm permit", "new mexico"],
    "KYU Permit": ["kyu", "ky permit", "kentucky"],
}


@dataclass(frozen=True)
class DriveSyncResult:
    companies: int = 0
    drivers: int = 0
    trucks: int = 0
    driver_documents: int = 0
    truck_documents: int = 0


def normalize(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[_\-]+", " ", value)
    value = re.sub(r"\.[a-z0-9]{1,8}$", "", value)
    value = re.sub(r"[^a-z0-9# ]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def titleize_folder_name(name: str) -> str:
    cleaned = re.sub(r"[_]+", " ", name).strip()
    return cleaned or name


def document_type_for_name(document_name: str) -> DocumentType:
    lowered = document_name.lower()
    if lowered == "cdl":
        return DocumentType.CDL
    if lowered in {"med card", "medical card", "medical examination certificate"}:
        return DocumentType.MEDICAL_CARD
    return DocumentType.OTHER


def guess_document_name(file_name: str, required_names: list[str]) -> str | None:
    haystack = normalize(file_name)
    for name in required_names:
        needles = [name, *DOCUMENT_ALIASES.get(name, [])]
        for needle in needles:
            if normalize(needle) and normalize(needle) in haystack:
                return canonical_document_name(name)
    return None


def is_truck_folder_name(name: str) -> bool:
    text = normalize(name)
    if text in TRUCK_CONTAINER_NAMES:
        return True
    if re.search(r"\b(unit|truck)\s*#?\s*[a-z0-9-]+", text):
        return True
    if re.fullmatch(r"[0-9]{2,6}", text):
        return True
    if re.search(r"\bvin\b", text):
        return True
    return False


def is_driver_container(name: str) -> bool:
    return normalize(name) in DRIVER_CONTAINER_NAMES


def is_truck_container(name: str) -> bool:
    return normalize(name) in TRUCK_CONTAINER_NAMES


def is_status_container(name: str) -> bool:
    return normalize(name) in STATUS_CONTAINER_NAMES


def folder_is_inactive(name: str) -> bool:
    text = normalize(name)
    return "inactive" in text or "not active" in text


def web_view_link_for_folder(folder_id: str) -> str:
    return f"https://drive.google.com/drive/folders/{folder_id}"


def web_view_link_for_file(item: DriveItem) -> str:
    return item.web_view_link or f"https://drive.google.com/file/d/{item.id}/view"


def scan_drive_to_database(db: Session, *, verbose: bool = False) -> DriveSyncResult:
    drive = GoogleDriveStorage()
    if not drive.enabled:
        raise RuntimeError("Google Drive is not configured")

    companies = 0
    drivers = 0
    trucks = 0
    driver_documents = 0
    truck_documents = 0

    company_folders = [item for item in drive.list_children() if item.is_folder]
    for company_folder in company_folders:
        if verbose:
            print(f"Scanning company: {company_folder.name}", flush=True)
        company = get_or_create_company(db, titleize_folder_name(company_folder.name))
        companies += 1
        children = drive.list_children(parent_id=company_folder.id)

        driver_roots = [item for item in children if item.is_folder and is_driver_container(item.name)]
        truck_roots = [item for item in children if item.is_folder and is_truck_container(item.name)]
        status_roots = [item for item in children if item.is_folder and is_status_container(item.name)]
        grouped_ids = {item.id for item in driver_roots + truck_roots + status_roots}
        direct_folders = [item for item in children if item.is_folder and item.id not in grouped_ids]

        for root in driver_roots:
            found_drivers, found_docs = sync_driver_container(db, drive, company.name, root, verbose=verbose)
            drivers += found_drivers
            driver_documents += found_docs

        for root in status_roots:
            found_drivers, found_docs = sync_driver_container(
                db,
                drive,
                company.name,
                root,
                verbose=verbose,
                inactive=folder_is_inactive(root.name),
            )
            drivers += found_drivers
            driver_documents += found_docs

        for root in truck_roots:
            found_trucks, found_docs = sync_truck_container(db, drive, company.name, root, verbose=verbose)
            trucks += found_trucks
            truck_documents += found_docs

        for folder in direct_folders:
            if is_truck_folder_name(folder.name):
                if verbose:
                    print(f"  truck folder: {folder.name}", flush=True)
                truck, count = sync_truck_folder(db, drive, company.name, folder)
                if truck:
                    trucks += 1
                    truck_documents += count
            else:
                if verbose:
                    print(f"  driver folder: {folder.name}", flush=True)
                driver, count = sync_driver_folder(
                    db,
                    drive,
                    company.name,
                    folder,
                    inactive=folder_is_inactive(folder.name),
                )
                if driver:
                    drivers += 1
                    driver_documents += count

    db.commit()
    return DriveSyncResult(
        companies=companies,
        drivers=drivers,
        trucks=trucks,
        driver_documents=driver_documents,
        truck_documents=truck_documents,
    )


def sync_driver_folder(
    db: Session,
    drive: GoogleDriveStorage,
    company_name: str,
    folder: DriveItem,
    inactive: bool = False,
) -> tuple[Driver | None, int]:
    from app.services.drivers import upsert_driver

    driver = upsert_driver(
        db,
        company_name=company_name,
        telegram_id=None,
        telegram_username=None,
        full_name=titleize_folder_name(folder.name),
        birth_date=None,
        phone=None,
        license_number=None,
    )
    driver.drive_folder_id = folder.id
    driver.drive_folder_url = web_view_link_for_folder(folder.id)
    driver.group_inactive = inactive

    required_names = list_required_document_names(db, company_id=driver.company_id, entity_type=EntityType.DRIVER)
    count = 0
    for file_item in flatten_files(drive, folder.id):
        document_name = guess_document_name(file_item.name, required_names)
        if not document_name:
            continue
        add_document_record(
            db,
            driver=driver,
            document_type=document_type_for_name(document_name),
            document_name=document_name,
            google_drive_file_id=file_item.id,
            google_drive_url=web_view_link_for_file(file_item),
            original_filename=file_item.name,
            mime_type=file_item.mime_type,
        )
        count += 1
    refresh_driver_status_from_db(db, driver)
    return driver, count


def sync_driver_container(
    db: Session,
    drive: GoogleDriveStorage,
    company_name: str,
    root: DriveItem,
    *,
    verbose: bool = False,
    inactive: bool = False,
) -> tuple[int, int]:
    driver_count = 0
    document_count = 0
    for folder in [item for item in drive.list_children(parent_id=root.id) if item.is_folder]:
        if is_status_container(folder.name):
            nested_drivers, nested_docs = sync_driver_container(
                db,
                drive,
                company_name,
                folder,
                verbose=verbose,
                inactive=inactive or folder_is_inactive(folder.name),
            )
            driver_count += nested_drivers
            document_count += nested_docs
            continue
        if verbose:
            label = "inactive driver folder" if inactive else "driver folder"
            print(f"  {label}: {folder.name}", flush=True)
        driver, count = sync_driver_folder(db, drive, company_name, folder, inactive=inactive)
        if driver:
            driver_count += 1
            document_count += count
    return driver_count, document_count


def sync_truck_folder(
    db: Session,
    drive: GoogleDriveStorage,
    company_name: str,
    folder: DriveItem,
) -> tuple[Truck | None, int]:
    unit_number = parse_unit_number(folder.name)
    truck = upsert_truck(
        db,
        company_name=company_name,
        unit_number=unit_number,
        drive_folder_id=folder.id,
        drive_folder_url=web_view_link_for_folder(folder.id),
    )
    required_names = list_required_document_names(db, company_id=truck.company_id, entity_type=EntityType.TRUCK)
    count = 0
    for file_item in flatten_files(drive, folder.id):
        document_name = guess_document_name(file_item.name, required_names)
        if not document_name:
            continue
        add_truck_document_record(
            db,
            truck=truck,
            document_name=document_name,
            google_drive_file_id=file_item.id,
            google_drive_url=web_view_link_for_file(file_item),
            original_filename=file_item.name,
            mime_type=file_item.mime_type,
        )
        count += 1
    refresh_truck_status_from_db(db, truck)
    return truck, count


def sync_truck_container(
    db: Session,
    drive: GoogleDriveStorage,
    company_name: str,
    root: DriveItem,
    *,
    verbose: bool = False,
) -> tuple[int, int]:
    truck_count = 0
    document_count = 0
    for folder in [item for item in drive.list_children(parent_id=root.id) if item.is_folder]:
        if is_status_container(folder.name):
            nested_trucks, nested_docs = sync_truck_container(
                db,
                drive,
                company_name,
                folder,
                verbose=verbose,
            )
            truck_count += nested_trucks
            document_count += nested_docs
            continue
        if verbose:
            print(f"  truck folder: {folder.name}", flush=True)
        truck, count = sync_truck_folder(db, drive, company_name, folder)
        if truck:
            truck_count += 1
            document_count += count
    return truck_count, document_count


def parse_unit_number(folder_name: str) -> str:
    text = titleize_folder_name(folder_name)
    match = re.search(r"(?:unit|truck)\s*#?\s*([a-zA-Z0-9-]+)", text, re.IGNORECASE)
    if match:
        return match.group(1)
    return text


def flatten_files(drive: GoogleDriveStorage, folder_id: str, depth: int = 0) -> list[DriveItem]:
    if depth > 1:
        return []
    files: list[DriveItem] = []
    for item in drive.list_children(parent_id=folder_id):
        if item.is_folder:
            files.extend(flatten_files(drive, item.id, depth + 1))
        else:
            files.append(item)
    return files
