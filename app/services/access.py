from fastapi import HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Driver, Truck, User, UserRole
from app.security import read_session_token


SESSION_COOKIE = "tmc_session"


def get_current_user_from_request(request: Request, db: Session) -> User | None:
    user_id = read_session_token(request.cookies.get(SESSION_COOKIE))
    if user_id is None:
        return None
    return db.get(User, user_id)


def require_user(request: Request, db: Session) -> User:
    user = get_current_user_from_request(request, db)
    if not user:
        raise HTTPException(status_code=status.HTTP_303_SEE_OTHER, headers={"Location": "/login"})
    return user


def require_superadmin(user: User) -> None:
    if user.role != UserRole.SUPERADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Superadmin access required")


def driver_query_for_user(user: User):
    stmt = select(Driver)
    if user.role == UserRole.MANAGER:
        stmt = stmt.where(Driver.company_id == user.company_id)
    return stmt


def ensure_driver_access(user: User, driver: Driver | None) -> Driver:
    if driver is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Driver not found")
    if user.role == UserRole.MANAGER and driver.company_id != user.company_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return driver


def ensure_truck_access(user: User, truck: Truck | None) -> Truck:
    if truck is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Truck not found")
    if user.role == UserRole.MANAGER and truck.company_id != user.company_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    return truck
