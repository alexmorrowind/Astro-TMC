from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import BigInteger, Date, DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class UserRole(StrEnum):
    SUPERADMIN = "superadmin"
    MANAGER = "manager"


class DriverStatus(StrEnum):
    ACTIVE = "Active"
    PENDING = "Pending"
    MISSING_DOCS = "Missing_Docs"


class EntityType(StrEnum):
    DRIVER = "driver"
    TRUCK = "truck"


class DocumentType(StrEnum):
    CDL = "CDL"
    MEDICAL_CARD = "Medical Card"
    OTHER = "Other"


class IncomingDocumentStatus(StrEnum):
    NEW = "new"
    ASSIGNED = "assigned"
    IGNORED = "ignored"


class OutboxStatus(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    is_hidden: Mapped[bool] = mapped_column(default=False)
    terminated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    drivers: Mapped[list["Driver"]] = relationship(back_populates="company", cascade="all, delete-orphan")
    trucks: Mapped[list["Truck"]] = relationship(back_populates="company", cascade="all, delete-orphan")
    users: Mapped[list["User"]] = relationship(back_populates="company")
    telegram_groups: Mapped[list["TelegramGroup"]] = relationship(back_populates="company")
    required_documents: Mapped[list["RequiredDocument"]] = relationship(back_populates="company")
    incoming_telegram_documents: Mapped[list["IncomingTelegramDocument"]] = relationship(
        back_populates="company"
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.MANAGER)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    company: Mapped[Company | None] = relationship(back_populates="users")


class Driver(Base):
    __tablename__ = "drivers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    telegram_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)
    telegram_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    full_name: Mapped[str] = mapped_column(String(255), index=True)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    emergency_contact: Mapped[str | None] = mapped_column(String(255), nullable=True)
    license_number: Mapped[str | None] = mapped_column(String(100), nullable=True)
    group_inactive: Mapped[bool] = mapped_column(default=False)
    status: Mapped[DriverStatus] = mapped_column(Enum(DriverStatus), default=DriverStatus.PENDING)
    terminated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    drive_folder_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    drive_folder_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    company: Mapped[Company] = relationship(back_populates="drivers")
    documents: Mapped[list["Document"]] = relationship(back_populates="driver", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("company_id", "full_name", name="uq_driver_company_full_name"),)


class Truck(Base):
    __tablename__ = "trucks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    unit_number: Mapped[str] = mapped_column(String(100), index=True)
    year: Mapped[str | None] = mapped_column(String(20), nullable=True)
    make: Mapped[str | None] = mapped_column(String(100), nullable=True)
    vin: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    license_plate: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[DriverStatus] = mapped_column(Enum(DriverStatus), default=DriverStatus.PENDING)
    terminated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    drive_folder_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    drive_folder_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    company: Mapped[Company] = relationship(back_populates="trucks")
    documents: Mapped[list["TruckDocument"]] = relationship(back_populates="truck", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint("company_id", "unit_number", name="uq_truck_company_unit_number"),)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    driver_id: Mapped[int] = mapped_column(ForeignKey("drivers.id"), index=True)
    document_type: Mapped[DocumentType] = mapped_column(Enum(DocumentType))
    document_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    google_drive_file_id: Mapped[str] = mapped_column(String(255))
    google_drive_url: Mapped[str] = mapped_column(Text)
    local_file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expiration_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    driver: Mapped[Driver] = relationship(back_populates="documents")

    @property
    def display_name(self) -> str:
        return self.document_name or self.document_type.value


class RequiredDocument(Base):
    __tablename__ = "required_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True, index=True)
    entity_type: Mapped[EntityType] = mapped_column(Enum(EntityType), default=EntityType.DRIVER, index=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    company: Mapped[Company | None] = relationship(back_populates="required_documents")

    __table_args__ = (
        UniqueConstraint("company_id", "entity_type", "name", name="uq_required_document_company_entity_name"),
    )


class TruckDocument(Base):
    __tablename__ = "truck_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    truck_id: Mapped[int] = mapped_column(ForeignKey("trucks.id"), index=True)
    document_name: Mapped[str] = mapped_column(String(255), index=True)
    google_drive_file_id: Mapped[str] = mapped_column(String(255))
    google_drive_url: Mapped[str] = mapped_column(Text)
    local_file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expiration_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    truck: Mapped[Truck] = relationship(back_populates="documents")

    @property
    def display_name(self) -> str:
        return self.document_name


class TelegramGroup(Base):
    __tablename__ = "telegram_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    chat_type: Mapped[str] = mapped_column(String(50))
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True, index=True)
    driver_id: Mapped[int | None] = mapped_column(ForeignKey("drivers.id"), nullable=True, index=True)
    linked_by_telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    linked_by_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_message_from: Mapped[str | None] = mapped_column(String(255), nullable=True)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    company: Mapped[Company | None] = relationship(back_populates="telegram_groups")
    driver: Mapped[Driver | None] = relationship(foreign_keys=[driver_id])


class TelegramOutboxMessage(Base):
    __tablename__ = "telegram_outbox_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    text: Mapped[str] = mapped_column(Text)
    photo_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[OutboxStatus] = mapped_column(Enum(OutboxStatus), default=OutboxStatus.PENDING, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class IncomingTelegramDocument(Base):
    __tablename__ = "incoming_telegram_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[IncomingDocumentStatus] = mapped_column(
        Enum(IncomingDocumentStatus),
        default=IncomingDocumentStatus.NEW,
        index=True,
    )
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"), nullable=True, index=True)
    assigned_driver_id: Mapped[int | None] = mapped_column(ForeignKey("drivers.id"), nullable=True)
    assigned_document_id: Mapped[int | None] = mapped_column(ForeignKey("documents.id"), nullable=True)

    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    telegram_chat_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    telegram_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sender_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sender_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sender_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    file_name: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    local_file_path: Mapped[str] = mapped_column(Text)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    assigned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    company: Mapped[Company | None] = relationship(back_populates="incoming_telegram_documents")
    assigned_driver: Mapped[Driver | None] = relationship(foreign_keys=[assigned_driver_id])
    assigned_document: Mapped[Document | None] = relationship(foreign_keys=[assigned_document_id])
