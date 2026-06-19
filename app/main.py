import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.database import get_db
from app.init_db import init_db
from app.models import (
    Company,
    Document,
    DocumentType,
    Driver,
    DriverStatus,
    EntityType,
    IncomingTelegramDocument,
    OutboxStatus,
    RequiredDocument,
    TelegramGroup,
    TelegramOutboxMessage,
    Truck,
    TruckDocument,
    User,
    UserRole,
)
from app.security import create_session_token, hash_password, verify_password
from app.services.access import (
    SESSION_COOKIE,
    ensure_driver_access,
    ensure_truck_access,
    get_current_user_from_request,
    require_superadmin,
    require_user,
)
from app.services.drivers import (
    list_drivers_with_documents,
    list_required_document_names,
    refresh_driver_status_from_db,
)
from app.services.drive import GoogleDriveStorage
from app.services.drive_sync import scan_drive_to_database
from app.services.groups import list_groups_for_company
from app.services.incoming import IncomingTelegramMetadata, assign_incoming_document, store_incoming_document
from app.services.outbox import create_outbox_messages
from app.services.trucks import list_trucks_with_documents


app = FastAPI(title="TMC Driver Docs")
templates = Jinja2Templates(directory="app/web/templates")
app.mount("/static", StaticFiles(directory="app/web/static"), name="static")

DEFAULT_REQUEST_PHOTO = Path("app/web/static/assets/hiring_driver_documents.jpg")


@app.on_event("startup")
def on_startup() -> None:
    init_db()


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def clean_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    clean_value = " ".join(value.strip().split())
    return clean_value or None


def clean_optional_multiline(value: str | None) -> str | None:
    if value is None:
        return None
    clean_value = value.strip()
    return clean_value or None


def render(request: Request, template: str, context: dict, status_code: int = 200) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        template,
        context,
        status_code=status_code,
    )


def company_summaries(db: Session, include_hidden: bool = False) -> list[dict]:
    company_stmt = select(Company).order_by(Company.name)
    if not include_hidden:
        company_stmt = company_stmt.where(
            Company.is_hidden == False,
            Company.terminated_at.is_(None),
        )
    companies = list(db.scalars(company_stmt))

    summaries = []
    for company in companies:
        drivers_count = (
            db.scalar(
                select(func.count(Driver.id)).where(
                    Driver.company_id == company.id,
                    Driver.terminated_at.is_(None),
                )
            )
            or 0
        )
        trucks_count = (
            db.scalar(
                select(func.count(Truck.id)).where(
                    Truck.company_id == company.id,
                    Truck.terminated_at.is_(None),
                )
            )
            or 0
        )
        missing_drivers = (
            db.scalar(
                select(func.count(Driver.id)).where(
                    Driver.company_id == company.id,
                    Driver.status == DriverStatus.MISSING_DOCS,
                    Driver.terminated_at.is_(None),
                )
            )
            or 0
        )
        inactive_drivers = (
            db.scalar(
                select(func.count(Driver.id)).where(
                    Driver.company_id == company.id,
                    Driver.group_inactive == True,
                    Driver.terminated_at.is_(None),
                )
            )
            or 0
        )
        summaries.append(
            {
                "company": company,
                "drivers": drivers_count,
                "trucks": trucks_count,
                "missing_drivers": missing_drivers,
                "inactive_drivers": inactive_drivers,
            }
        )
    return summaries


def document_state(document: Document | TruckDocument | None) -> dict:
    if not document:
        return {"label": "Missing", "detail": "No file", "css": "missing", "expiration": None}
    if not document.expiration_date:
        return {
            "label": "No exp date",
            "detail": "Set date",
            "css": "no-exp",
            "expiration": None,
        }
    today = date.today()
    if document.expiration_date < today:
        css = "expired"
        label = "Expired"
    elif document.expiration_date <= today + timedelta(days=30):
        css = "expiring"
        label = "Expiring"
    else:
        css = "valid"
        label = "Valid"
    return {
        "label": label,
        "detail": document.expiration_date.isoformat(),
        "css": css,
        "expiration": document.expiration_date,
    }


def driver_row_payload(driver: Driver, required_documents: list[str]) -> dict:
    document_map = {document.display_name: document for document in driver.documents}
    documents = [
        {
            "name": document_name,
            "document": document_map.get(document_name),
            "state": document_state(document_map.get(document_name)),
        }
        for document_name in required_documents
    ]
    missing_documents = [item["name"] for item in documents if item["document"] is None]
    return {"item": driver, "documents": documents, "missing_documents": missing_documents}


def truck_row_payload(truck: Truck, required_documents: list[str]) -> dict:
    document_map = {document.display_name: document for document in truck.documents}
    documents = [
        {
            "name": document_name,
            "document": document_map.get(document_name),
            "state": document_state(document_map.get(document_name)),
        }
        for document_name in required_documents
    ]
    missing_documents = [item["name"] for item in documents if item["document"] is None]
    return {"item": truck, "documents": documents, "missing_documents": missing_documents}


@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    company_id: int | None = None,
    status_filter: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    companies_stmt = select(Company).order_by(Company.name)
    if user.role == UserRole.SUPERADMIN:
        companies_stmt = companies_stmt.where(Company.is_hidden == False)
    companies = list(db.scalars(companies_stmt))
    effective_company_id = user.company_id if user.role == UserRole.MANAGER else company_id

    drivers = list_drivers_with_documents(db, company_id=effective_company_id)
    drivers = [driver for driver in drivers if driver.terminated_at is None]
    if user.role == UserRole.SUPERADMIN and effective_company_id is None:
        drivers = []
    if user.role == UserRole.SUPERADMIN:
        drivers = [
            driver
            for driver in drivers
            if not driver.company.is_hidden and driver.company.terminated_at is None
        ]
    if status_filter:
        drivers = [driver for driver in drivers if driver.status.value == status_filter]
    if q:
        needle = q.strip().lower()
        drivers = [
            driver
            for driver in drivers
            if needle in driver.full_name.lower()
            or needle in (driver.phone or "").lower()
            or needle in (driver.license_number or "").lower()
        ]

    stats = {
        "total": len(drivers),
        "active": sum(1 for driver in drivers if driver.status == DriverStatus.ACTIVE),
        "missing": sum(1 for driver in drivers if driver.status == DriverStatus.MISSING_DOCS),
        "pending": sum(1 for driver in drivers if driver.status == DriverStatus.PENDING),
    }
    required_documents = list_required_document_names(
        db,
        company_id=effective_company_id,
        entity_type=EntityType.DRIVER,
    )
    driver_rows = [driver_row_payload(driver, required_documents) for driver in drivers]
    return render(
        request,
        "dashboard.html",
        {
            "user": user,
            "companies": companies,
            "drivers": drivers,
            "driver_rows": driver_rows,
            "stats": stats,
            "company_summaries": company_summaries(db) if user.role == UserRole.SUPERADMIN else [],
            "required_documents": required_documents,
            "selected_company_id": effective_company_id,
            "status_filter": status_filter or "",
            "q": q or "",
            "statuses": list(DriverStatus),
            "drive_sync_result": request.query_params.get("drive_sync"),
        },
    )


@app.get("/trucks", response_class=HTMLResponse)
def trucks_page(
    request: Request,
    company_id: int | None = None,
    status_filter: str | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    companies_stmt = select(Company).order_by(Company.name)
    if user.role == UserRole.SUPERADMIN:
        companies_stmt = companies_stmt.where(Company.is_hidden == False)
    companies = list(db.scalars(companies_stmt))
    effective_company_id = user.company_id if user.role == UserRole.MANAGER else company_id
    trucks = list_trucks_with_documents(db, company_id=effective_company_id)
    trucks = [truck for truck in trucks if truck.terminated_at is None]
    if user.role == UserRole.SUPERADMIN and effective_company_id is None:
        trucks = []
    if user.role == UserRole.SUPERADMIN:
        trucks = [
            truck
            for truck in trucks
            if not truck.company.is_hidden and truck.company.terminated_at is None
        ]
    if status_filter:
        trucks = [truck for truck in trucks if truck.status.value == status_filter]
    if q:
        needle = q.strip().lower()
        trucks = [
            truck
            for truck in trucks
            if needle in truck.unit_number.lower()
            or needle in (truck.vin or "").lower()
            or needle in (truck.license_plate or "").lower()
            or needle in (truck.make or "").lower()
        ]

    stats = {
        "total": len(trucks),
        "active": sum(1 for truck in trucks if truck.status == DriverStatus.ACTIVE),
        "missing": sum(1 for truck in trucks if truck.status == DriverStatus.MISSING_DOCS),
        "pending": sum(1 for truck in trucks if truck.status == DriverStatus.PENDING),
    }
    required_documents = list_required_document_names(
        db,
        company_id=effective_company_id,
        entity_type=EntityType.TRUCK,
    )
    truck_rows = [truck_row_payload(truck, required_documents) for truck in trucks]
    return render(
        request,
        "trucks.html",
        {
            "user": user,
            "companies": companies,
            "trucks": trucks,
            "truck_rows": truck_rows,
            "stats": stats,
            "company_summaries": company_summaries(db) if user.role == UserRole.SUPERADMIN else [],
            "required_documents": required_documents,
            "selected_company_id": effective_company_id,
            "status_filter": status_filter or "",
            "q": q or "",
            "statuses": list(DriverStatus),
            "drive_sync_result": request.query_params.get("drive_sync"),
        },
    )


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, db: Session = Depends(get_db)):
    if get_current_user_from_request(request, db):
        return RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    return render(request, "login.html", {"user": None, "error": None})


@app.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.username == username))
    if not user or not verify_password(password, user.password_hash):
        return render(
            request,
            "login.html",
            {"user": None, "error": "Неверный логин или пароль"},
            status_code=status.HTTP_401_UNAUTHORIZED,
        )

    response = RedirectResponse("/", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        SESSION_COOKIE,
        create_session_token(user.id),
        httponly=True,
        samesite="lax",
    )
    return response


@app.post("/logout")
def logout() -> Response:
    response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/drivers/{driver_id}", response_class=HTMLResponse)
def driver_detail(driver_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    driver = db.scalar(
        select(Driver)
        .where(Driver.id == driver_id)
        .options(joinedload(Driver.company), joinedload(Driver.documents))
    )
    driver = ensure_driver_access(user, driver)
    return render(
        request,
        "driver_detail.html",
        {
            "user": user,
            "driver": driver,
            "required_documents": list_required_document_names(
                db,
                company_id=driver.company_id,
                entity_type=EntityType.DRIVER,
            ),
            "document_types": list(DocumentType),
        },
    )


@app.post("/drivers/{driver_id}/activity")
def update_driver_activity(
    driver_id: int,
    request: Request,
    group_inactive: str = Form("false"),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    driver = db.scalar(select(Driver).where(Driver.id == driver_id).options(joinedload(Driver.company)))
    driver = ensure_driver_access(user, driver)
    driver.group_inactive = group_inactive == "true"
    db.commit()
    return RedirectResponse(f"/drivers/{driver.id}", status_code=303)


@app.post("/drivers/{driver_id}/profile")
def update_driver_profile(
    driver_id: int,
    request: Request,
    birth_date: str | None = Form(None),
    address: str | None = Form(None),
    email: str | None = Form(None),
    phone: str | None = Form(None),
    emergency_contact: str | None = Form(None),
    license_number: str | None = Form(None),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    driver = db.scalar(select(Driver).where(Driver.id == driver_id).options(joinedload(Driver.company)))
    driver = ensure_driver_access(user, driver)
    driver.birth_date = parse_date(birth_date)
    driver.address = clean_optional_multiline(address)
    driver.email = clean_optional_text(email)
    driver.phone = clean_optional_text(phone)
    driver.emergency_contact = clean_optional_text(emergency_contact)
    driver.license_number = clean_optional_text(license_number)
    db.commit()
    return RedirectResponse(f"/drivers/{driver.id}", status_code=303)


@app.post("/drivers/{driver_id}/terminate")
def terminate_driver(driver_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    driver = db.scalar(select(Driver).where(Driver.id == driver_id).options(joinedload(Driver.company)))
    driver = ensure_driver_access(user, driver)
    driver.terminated_at = datetime.utcnow()
    db.commit()
    return RedirectResponse(f"/?company_id={driver.company_id}", status_code=303)


@app.post("/drivers/{driver_id}/restore")
def restore_driver(driver_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    driver = db.scalar(select(Driver).where(Driver.id == driver_id).options(joinedload(Driver.company)))
    driver = ensure_driver_access(user, driver)
    driver.terminated_at = None
    db.commit()
    return RedirectResponse("/terminated", status_code=303)


@app.get("/trucks/{truck_id}", response_class=HTMLResponse)
def truck_detail(truck_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    truck = db.scalar(
        select(Truck)
        .where(Truck.id == truck_id)
        .options(joinedload(Truck.company), joinedload(Truck.documents))
    )
    truck = ensure_truck_access(user, truck)
    return render(
        request,
        "truck_detail.html",
        {
            "user": user,
            "truck": truck,
            "required_documents": list_required_document_names(
                db,
                company_id=truck.company_id,
                entity_type=EntityType.TRUCK,
            ),
        },
    )


@app.post("/trucks/{truck_id}/profile")
def update_truck_profile(
    truck_id: int,
    request: Request,
    year: str | None = Form(None),
    make: str | None = Form(None),
    vin: str | None = Form(None),
    license_plate: str | None = Form(None),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    truck = db.scalar(select(Truck).where(Truck.id == truck_id).options(joinedload(Truck.company)))
    truck = ensure_truck_access(user, truck)
    truck.year = clean_optional_text(year)
    truck.make = clean_optional_text(make)
    truck.vin = clean_optional_text(vin)
    truck.license_plate = clean_optional_text(license_plate)
    db.commit()
    return RedirectResponse(f"/trucks/{truck.id}", status_code=303)


@app.post("/trucks/{truck_id}/terminate")
def terminate_truck(truck_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    truck = db.scalar(select(Truck).where(Truck.id == truck_id).options(joinedload(Truck.company)))
    truck = ensure_truck_access(user, truck)
    truck.terminated_at = datetime.utcnow()
    db.commit()
    return RedirectResponse(f"/trucks?company_id={truck.company_id}", status_code=303)


@app.post("/trucks/{truck_id}/restore")
def restore_truck(truck_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    truck = db.scalar(select(Truck).where(Truck.id == truck_id).options(joinedload(Truck.company)))
    truck = ensure_truck_access(user, truck)
    truck.terminated_at = None
    db.commit()
    return RedirectResponse("/terminated", status_code=303)


@app.post("/drive-sync")
def sync_drive_route(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    require_superadmin(user)
    try:
        result = scan_drive_to_database(db)
    except Exception as exc:
        return RedirectResponse(f"/?drive_sync=error:{exc}", status_code=303)
    message = (
        f"companies={result.companies},drivers={result.drivers},"
        f"trucks={result.trucks},driver_docs={result.driver_documents},"
        f"truck_docs={result.truck_documents}"
    )
    return RedirectResponse(f"/?drive_sync={message}", status_code=303)


@app.get("/companies", response_class=HTMLResponse)
def companies_page(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    require_superadmin(user)
    return render(
        request,
        "companies.html",
        {
            "user": user,
            "company_summaries": company_summaries(db, include_hidden=True),
        },
    )


@app.post("/companies")
def create_company_from_companies_page(
    request: Request,
    name: str = Form(...),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    require_superadmin(user)
    clean_name = " ".join(name.strip().split())
    if clean_name:
        company = db.scalar(select(Company).where(Company.name == clean_name))
        if company:
            company.is_hidden = False
        else:
            db.add(Company(name=clean_name))
        db.commit()
    return RedirectResponse("/companies", status_code=303)


@app.post("/companies/{company_id}/toggle-hidden")
def toggle_company_hidden(company_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    require_superadmin(user)
    company = db.get(Company, company_id)
    if company:
        company.is_hidden = not company.is_hidden
        db.commit()
    return RedirectResponse("/companies", status_code=303)


@app.post("/companies/{company_id}/terminate")
def terminate_company(company_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    require_superadmin(user)
    company = db.get(Company, company_id)
    if company:
        company.terminated_at = datetime.utcnow()
        db.commit()
    return RedirectResponse("/", status_code=303)


@app.post("/companies/{company_id}/restore")
def restore_company(company_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    require_superadmin(user)
    company = db.get(Company, company_id)
    if company:
        company.terminated_at = None
        company.is_hidden = False
        db.commit()
    return RedirectResponse("/terminated", status_code=303)


@app.get("/terminated", response_class=HTMLResponse)
def terminated_page(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    effective_company_id = user.company_id if user.role == UserRole.MANAGER else None
    companies_stmt = select(Company).where(Company.terminated_at.is_not(None)).order_by(Company.name)
    if effective_company_id:
        companies_stmt = companies_stmt.where(Company.id == effective_company_id)
    drivers_stmt = (
        select(Driver)
        .where(Driver.terminated_at.is_not(None))
        .options(joinedload(Driver.company))
        .order_by(Driver.terminated_at.desc())
    )
    trucks_stmt = (
        select(Truck)
        .where(Truck.terminated_at.is_not(None))
        .options(joinedload(Truck.company))
        .order_by(Truck.terminated_at.desc())
    )
    if effective_company_id:
        drivers_stmt = drivers_stmt.where(Driver.company_id == effective_company_id)
        trucks_stmt = trucks_stmt.where(Truck.company_id == effective_company_id)
    return render(
        request,
        "terminated.html",
        {
            "user": user,
            "companies": list(db.scalars(companies_stmt)),
            "drivers": list(db.scalars(drivers_stmt)),
            "trucks": list(db.scalars(trucks_stmt)),
        },
    )


@app.get("/telegram/groups", response_class=HTMLResponse)
def telegram_groups_page(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    effective_company_id = user.company_id if user.role == UserRole.MANAGER else None
    groups = list_groups_for_company(db, company_id=effective_company_id)
    companies = list(db.scalars(select(Company).order_by(Company.name)))
    drivers = list_drivers_with_documents(db, company_id=effective_company_id)
    return render(
        request,
        "telegram_groups.html",
        {
            "user": user,
            "groups": groups,
            "companies": companies,
            "drivers": drivers,
        },
    )


@app.post("/telegram/groups")
def create_telegram_group(
    request: Request,
    chat_id: int = Form(...),
    title: str = Form(...),
    chat_type: str = Form("group"),
    company_id: int | None = Form(None),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    require_superadmin(user)
    group = db.scalar(select(TelegramGroup).where(TelegramGroup.chat_id == chat_id))
    if group is None:
        group = TelegramGroup(chat_id=chat_id, title=title.strip(), chat_type=chat_type.strip() or "group")
        db.add(group)
    group.title = title.strip()
    group.chat_type = chat_type.strip() or "group"
    group.company_id = company_id
    group.is_active = True
    db.commit()
    return RedirectResponse("/telegram/groups", status_code=303)


@app.post("/telegram/groups/{group_id}/disable")
def disable_telegram_group(group_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    require_superadmin(user)
    group = db.get(TelegramGroup, group_id)
    if group:
        group.is_active = False
        db.commit()
    return RedirectResponse("/telegram/groups", status_code=303)


@app.post("/telegram/groups/{group_id}/enable")
def enable_telegram_group(group_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    require_superadmin(user)
    group = db.get(TelegramGroup, group_id)
    if group:
        group.is_active = True
        db.commit()
    return RedirectResponse("/telegram/groups", status_code=303)


@app.post("/telegram/groups/{group_id}/assign-driver")
def assign_group_driver(
    group_id: int,
    request: Request,
    driver_id: int | None = Form(None),
    company_id: int | None = Form(None),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    require_superadmin(user)
    group = db.get(TelegramGroup, group_id)
    if not group:
        raise HTTPException(status_code=404, detail="Chat not found")
    if driver_id:
        driver = db.get(Driver, driver_id)
        if not driver:
            raise HTTPException(status_code=404, detail="Driver not found")
        company_id = driver.company_id
    group.driver_id = driver_id
    group.company_id = company_id
    db.commit()
    return RedirectResponse("/telegram/groups", status_code=303)


@app.get("/telegram/request-docs", response_class=HTMLResponse)
def request_docs_page(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    effective_company_id = user.company_id if user.role == UserRole.MANAGER else None
    chats_stmt = select(TelegramGroup).where(TelegramGroup.chat_type == "private").order_by(TelegramGroup.title)
    if effective_company_id:
        chats_stmt = chats_stmt.where(TelegramGroup.company_id == effective_company_id)
    chats = list(db.scalars(chats_stmt))
    outbox_stmt = (
        select(TelegramOutboxMessage)
        .outerjoin(TelegramGroup, TelegramGroup.chat_id == TelegramOutboxMessage.chat_id)
        .order_by(TelegramOutboxMessage.created_at.desc())
        .limit(50)
    )
    if effective_company_id:
        outbox_stmt = outbox_stmt.where(TelegramGroup.company_id == effective_company_id)
    outbox_messages = list(db.scalars(outbox_stmt))
    message_templates = {
        "en": (
            "Hello. Please send updated driver documents here:\n"
            "- CDL\n"
            "- Medical Examination Certificate (Medical Card)\n"
            "- Social Security number (SSN)\n"
            "- Work Authorization / Green Card / US Passport\n"
            "- Emergency contact email and phone\n"
            "Thank you."
        ),
        "ru": (
            "Здравствуйте. Пожалуйста, отправьте обновленные документы водителя сюда:\n"
            "- CDL\n"
            "- Medical Examination Certificate (Medical Card)\n"
            "- Social Security number (SSN)\n"
            "- Work Authorization / Green Card / US Passport\n"
            "- Emergency contact email and phone\n"
            "Спасибо."
        ),
        "uz": (
            "Assalomu alaykum. Iltimos, yangilangan haydovchi hujjatlarini shu yerga yuboring:\n"
            "- CDL\n"
            "- Medical Examination Certificate (Medical Card)\n"
            "- Social Security number (SSN)\n"
            "- Work Authorization / Green Card / US Passport\n"
            "- Emergency contact email and phone\n"
            "Rahmat."
        ),
    }
    return render(
        request,
        "request_docs.html",
        {
            "user": user,
            "chats": chats,
            "default_text": message_templates["en"],
            "message_templates": message_templates,
            "default_photo_available": DEFAULT_REQUEST_PHOTO.exists(),
            "outbox_messages": outbox_messages,
        },
    )


@app.post("/telegram/request-docs")
async def queue_request_docs(
    request: Request,
    chat_ids: list[int] = Form(...),
    text: str = Form(...),
    use_default_photo: bool = Form(False),
    photo: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    effective_company_id = user.company_id if user.role == UserRole.MANAGER else None
    chats_stmt = select(TelegramGroup).where(
        TelegramGroup.chat_id.in_(chat_ids),
        TelegramGroup.chat_type == "private",
        TelegramGroup.is_active == True,
    )
    if effective_company_id:
        chats_stmt = chats_stmt.where(TelegramGroup.company_id == effective_company_id)
    chats = list(db.scalars(chats_stmt))
    if not chats:
        raise HTTPException(status_code=400, detail="No active private chats selected")

    photo_path = None
    photo_filename = None
    should_delete_photo_path = False
    if photo and photo.filename:
        suffix = Path(photo.filename).suffix or ".bin"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
            photo_path = temp_file.name
            photo_filename = photo.filename
            should_delete_photo_path = True
            while chunk := await photo.read(1024 * 1024):
                temp_file.write(chunk)
    elif use_default_photo and DEFAULT_REQUEST_PHOTO.exists():
        photo_path = str(DEFAULT_REQUEST_PHOTO)
        photo_filename = DEFAULT_REQUEST_PHOTO.name
    try:
        create_outbox_messages(
            db,
            chats=chats,
            text=text,
            photo_source_path=photo_path,
            photo_filename=photo_filename,
            user=user,
        )
        db.commit()
    finally:
        if should_delete_photo_path and photo_path:
            Path(photo_path).unlink(missing_ok=True)
    return RedirectResponse("/telegram/request-docs?queued=1", status_code=303)


@app.get("/telegram/incoming", response_class=HTMLResponse)
def incoming_documents_page(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    effective_company_id = user.company_id if user.role == UserRole.MANAGER else None

    incoming_stmt = (
        select(IncomingTelegramDocument)
        .options(joinedload(IncomingTelegramDocument.company))
        .order_by(IncomingTelegramDocument.created_at.desc())
    )
    if effective_company_id:
        incoming_stmt = incoming_stmt.where(IncomingTelegramDocument.company_id == effective_company_id)

    incoming_documents = list(db.scalars(incoming_stmt))
    drivers = list_drivers_with_documents(db, company_id=effective_company_id)
    return render(
        request,
        "incoming_documents.html",
        {
            "user": user,
            "incoming_documents": incoming_documents,
            "drivers": drivers,
            "document_types": list(DocumentType),
            "required_documents": list_required_document_names(
                db,
                company_id=effective_company_id,
                entity_type=EntityType.DRIVER,
            ),
        },
    )


@app.post("/telegram/incoming/{incoming_id}/assign")
def assign_incoming_document_route(
    incoming_id: int,
    request: Request,
    driver_id: int = Form(...),
    document_name: str = Form(...),
    expiration_date: str | None = Form(None),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    incoming = db.scalar(
        select(IncomingTelegramDocument)
        .where(IncomingTelegramDocument.id == incoming_id)
        .options(joinedload(IncomingTelegramDocument.company))
    )
    if not incoming:
        raise HTTPException(status_code=404, detail="Incoming document not found")
    if user.role == UserRole.MANAGER and incoming.company_id != user.company_id:
        raise HTTPException(status_code=403, detail="Access denied")

    driver = db.scalar(
        select(Driver)
        .where(Driver.id == driver_id)
        .options(joinedload(Driver.company), joinedload(Driver.documents))
    )
    driver = ensure_driver_access(user, driver)
    assign_incoming_document(
        db,
        incoming=incoming,
        driver=driver,
        document_name=document_name,
        expiration_date=parse_date(expiration_date),
    )
    db.commit()
    return RedirectResponse("/telegram/incoming", status_code=303)


@app.post("/documents/{document_id}/metadata")
def update_document_metadata(
    document_id: int,
    request: Request,
    expiration_date: str | None = Form(None),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    document = db.scalar(
        select(Document)
        .where(Document.id == document_id)
        .options(joinedload(Document.driver).joinedload(Driver.company))
    )
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    ensure_driver_access(user, document.driver)
    document.expiration_date = parse_date(expiration_date)
    db.commit()
    return RedirectResponse(f"/drivers/{document.driver_id}", status_code=303)


@app.post("/truck-documents/{document_id}/metadata")
def update_truck_document_metadata(
    document_id: int,
    request: Request,
    expiration_date: str | None = Form(None),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    document = db.scalar(
        select(TruckDocument)
        .where(TruckDocument.id == document_id)
        .options(joinedload(TruckDocument.truck).joinedload(Truck.company))
    )
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    ensure_truck_access(user, document.truck)
    document.expiration_date = parse_date(expiration_date)
    db.commit()
    return RedirectResponse(f"/trucks/{document.truck_id}", status_code=303)


@app.get("/documents/{document_id}/view")
def view_document(document_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    document = db.scalar(select(Document).where(Document.id == document_id).options(joinedload(Document.driver)))
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    ensure_driver_access(user, document.driver)
    ensure_document_local_file(db, document)
    return FileResponse(
        document.local_file_path,
        media_type=document.mime_type or "application/octet-stream",
        filename=document.original_filename,
        content_disposition_type="inline",
    )


@app.get("/documents/{document_id}/download")
def download_document(document_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    document = db.scalar(select(Document).where(Document.id == document_id).options(joinedload(Document.driver)))
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    ensure_driver_access(user, document.driver)
    ensure_document_local_file(db, document)
    return FileResponse(
        document.local_file_path,
        media_type=document.mime_type or "application/octet-stream",
        filename=document.original_filename or "document",
        content_disposition_type="attachment",
    )


@app.get("/truck-documents/{document_id}/view")
def view_truck_document(document_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    document = db.scalar(
        select(TruckDocument).where(TruckDocument.id == document_id).options(joinedload(TruckDocument.truck))
    )
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    ensure_truck_access(user, document.truck)
    ensure_truck_document_local_file(db, document)
    return FileResponse(
        document.local_file_path,
        media_type=document.mime_type or "application/octet-stream",
        filename=document.original_filename,
        content_disposition_type="inline",
    )


@app.get("/truck-documents/{document_id}/download")
def download_truck_document(document_id: int, request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    document = db.scalar(
        select(TruckDocument).where(TruckDocument.id == document_id).options(joinedload(TruckDocument.truck))
    )
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    ensure_truck_access(user, document.truck)
    ensure_truck_document_local_file(db, document)
    return FileResponse(
        document.local_file_path,
        media_type=document.mime_type or "application/octet-stream",
        filename=document.original_filename or "document",
        content_disposition_type="attachment",
    )


def ensure_document_local_file(db: Session, document: Document) -> None:
    if document.local_file_path and Path(document.local_file_path).exists():
        return

    cache_dir = Path("uploads/drive_cache")
    suffix = Path(document.original_filename or "").suffix or ".bin"
    destination = cache_dir / f"{document.id}_{document.google_drive_file_id}{suffix}"
    try:
        drive_name, mime_type = GoogleDriveStorage().download_file(
            file_id=document.google_drive_file_id,
            destination_path=destination,
        )
    except Exception as exc:
        if document.google_drive_url:
            raise HTTPException(
                status_code=502,
                detail=f"Could not download document from Google Drive: {exc}",
            ) from exc
        raise

    document.local_file_path = str(destination)
    document.original_filename = document.original_filename or drive_name
    document.mime_type = document.mime_type or mime_type
    db.commit()


def ensure_truck_document_local_file(db: Session, document: TruckDocument) -> None:
    if document.local_file_path and Path(document.local_file_path).exists():
        return

    cache_dir = Path("uploads/drive_cache/trucks")
    suffix = Path(document.original_filename or "").suffix or ".bin"
    destination = cache_dir / f"{document.id}_{document.google_drive_file_id}{suffix}"
    try:
        drive_name, mime_type = GoogleDriveStorage().download_file(
            file_id=document.google_drive_file_id,
            destination_path=destination,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not download truck document from Google Drive: {exc}",
        ) from exc

    document.local_file_path = str(destination)
    document.original_filename = document.original_filename or drive_name
    document.mime_type = document.mime_type or mime_type
    db.commit()


@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request, db: Session = Depends(get_db)):
    user = require_user(request, db)
    require_superadmin(user)
    companies = list(db.scalars(select(Company).order_by(Company.name)))
    users = list(db.scalars(select(User).options(joinedload(User.company)).order_by(User.username)))
    required_documents = list(
        db.scalars(
            select(RequiredDocument)
            .options(joinedload(RequiredDocument.company))
            .order_by(RequiredDocument.company_id, RequiredDocument.sort_order, RequiredDocument.name)
        )
    )
    return render(
        request,
        "admin.html",
        {
            "user": user,
            "companies": companies,
            "users": users,
            "roles": list(UserRole),
            "required_documents": required_documents,
            "entity_types": list(EntityType),
        },
    )


@app.post("/admin/companies")
def create_company(
    request: Request,
    name: str = Form(...),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    require_superadmin(user)
    clean_name = " ".join(name.strip().split())
    if clean_name:
        company = db.scalar(select(Company).where(Company.name == clean_name))
        if not company:
            db.add(Company(name=clean_name))
            db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/users")
def create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: UserRole = Form(...),
    company_id: int | None = Form(None),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    require_superadmin(user)
    if db.scalar(select(User).where(User.username == username)):
        return RedirectResponse("/admin?error=user_exists", status_code=303)
    manager_company_id = company_id if role == UserRole.MANAGER else None
    db.add(
        User(
            username=username.strip(),
            password_hash=hash_password(password),
            role=role,
            company_id=manager_company_id,
        )
    )
    db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/required-documents")
def create_required_document(
    request: Request,
    name: str = Form(...),
    entity_type: EntityType = Form(EntityType.DRIVER),
    company_id: int | None = Form(None),
    sort_order: int = Form(100),
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    require_superadmin(user)
    clean_name = " ".join(name.strip().split())
    if clean_name:
        existing = db.scalar(
            select(RequiredDocument).where(
                RequiredDocument.company_id == company_id,
                RequiredDocument.entity_type == entity_type,
                RequiredDocument.name == clean_name,
            )
        )
        if existing:
            existing.is_active = True
            existing.sort_order = sort_order
        else:
            db.add(
                RequiredDocument(
                    company_id=company_id,
                    entity_type=entity_type,
                    name=clean_name,
                    sort_order=sort_order,
                )
            )
        db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/required-documents/{required_document_id}/toggle")
def toggle_required_document(
    required_document_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    user = require_user(request, db)
    require_superadmin(user)
    required_document = db.get(RequiredDocument, required_document_id)
    if required_document:
        required_document.is_active = not required_document.is_active
        db.commit()
    return RedirectResponse("/admin", status_code=303)


@app.post("/api/telegram/documents/ingest")
async def ingest_telegram_document(
    file: UploadFile = File(...),
    chat_id: int = Form(...),
    chat_title: str | None = Form(None),
    message_id: int | None = Form(None),
    sender_id: int | None = Form(None),
    sender_username: str | None = Form(None),
    sender_name: str | None = Form(None),
    caption: str | None = Form(None),
    x_ingest_token: str | None = Header(None, alias="X-Ingest-Token"),
    db: Session = Depends(get_db),
):
    settings = get_settings()
    if settings.telegram_ingest_token and x_ingest_token != settings.telegram_ingest_token:
        raise HTTPException(status_code=401, detail="Invalid ingest token")

    suffix = Path(file.filename or "telegram_document").suffix or ".bin"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
        temp_path = Path(temp_file.name)
        while chunk := await file.read(1024 * 1024):
            temp_file.write(chunk)

    try:
        incoming = store_incoming_document(
            db,
            source_path=temp_path,
            metadata=IncomingTelegramMetadata(
                chat_id=chat_id,
                chat_title=chat_title,
                message_id=message_id,
                sender_id=sender_id,
                sender_username=sender_username,
                sender_name=sender_name,
                caption=caption,
                mime_type=file.content_type,
                file_name=file.filename,
            ),
        )
        db.commit()
    finally:
        temp_path.unlink(missing_ok=True)

    return {"id": incoming.id, "status": incoming.status.value}


@app.get("/api/integrations/status/")
def integration_status():
    settings = get_settings()
    return {
        "telegram_groups_connected": db_count_telegram_groups(),
        "telegram_private_chats_connected": db_count_telegram_groups(chat_type="private"),
        "telegram_outbox_pending": db_count_outbox(OutboxStatus.PENDING),
        "telegram_outbox_failed": db_count_outbox(OutboxStatus.FAILED),
        "companies": db_count_model(Company),
        "drivers": db_count_model(Driver),
        "driver_documents": db_count_model(Document),
        "trucks": db_count_model(Truck),
        "truck_documents": db_count_model(TruckDocument),
        "telegram_collector_configured": bool(settings.telegram_api_id and settings.telegram_api_hash),
        "google_credentials_found": bool(
            settings.google_credentials_json or Path(settings.google_credentials_file).exists()
        ),
        "google_drive_root_folder_configured": bool(settings.google_drive_root_folder_id),
        "database": "configured" if settings.database_url else "missing",
    }


def db_count_telegram_groups(chat_type: str | None = None) -> int:
    from sqlalchemy import func, select

    from app.database import SessionLocal

    with SessionLocal() as db:
        stmt = select(func.count(TelegramGroup.id)).where(TelegramGroup.is_active == True)
        if chat_type:
            stmt = stmt.where(TelegramGroup.chat_type == chat_type)
        return db.scalar(stmt) or 0


def db_count_outbox(status_value: OutboxStatus) -> int:
    from sqlalchemy import func, select

    from app.database import SessionLocal

    with SessionLocal() as db:
        return db.scalar(
            select(func.count(TelegramOutboxMessage.id)).where(TelegramOutboxMessage.status == status_value)
        ) or 0


def db_count_model(model) -> int:
    from sqlalchemy import func, select

    from app.database import SessionLocal

    with SessionLocal() as db:
        return db.scalar(select(func.count(model.id))) or 0
