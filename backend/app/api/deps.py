"""Authentication and role-based authorization dependencies."""
from __future__ import annotations

from collections.abc import Callable

import jwt
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.security import decode_access_token
from app.db.session import get_db
from app.models.entities import User
from app.models.enums import Role

SESSION_COOKIE = "pos_session"


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired session") from None
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="User inactive or missing")
    return user


def require_roles(*roles: Role) -> Callable[[User], User]:
    allowed = set(roles)

    def checker(user: User = Depends(current_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(status_code=403, detail=f"Requires role: {', '.join(sorted(allowed))}")
        return user

    return checker


OFFICER_ROLES = (Role.OFFICER, Role.ADMIN)
APPROVER_ROLES = (Role.APPROVER,)  # only the approver signs off; admins set the system up
DRIVE_ROLES = (Role.COORDINATOR, Role.OFFICER, Role.ADMIN)  # create drives, process company email, invite students
ANY_ROLE = (Role.ADMIN, Role.OFFICER, Role.APPROVER, Role.VIEWER, Role.COORDINATOR)
