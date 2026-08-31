import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.security import require_roles, get_password_hash, TokenData
from models.database import User, UserRole

router = APIRouter(prefix="/api/v1/users", tags=["Usuarios"])


class UserCreate(BaseModel):
    username: str
    email: EmailStr
    password: str
    role: str = "VIEWER"
    full_name: Optional[str] = None


class UserUpdate(BaseModel):
    role: Optional[str] = None  # Permite recibir el string del frontend
    is_active: Optional[bool] = None


class UserResponse(BaseModel):
    id: str
    username: str
    email: str
    role: str
    full_name: Optional[str] = None
    is_active: bool
    last_login: Optional[str] = None


def parse_user_role(role_str: str) -> UserRole:
    """Convierte cualquier string recibido a un tipo Enum UserRole de forma segura"""
    role_input = role_str.strip().upper()
    if hasattr(UserRole, role_input):
        return UserRole[role_input]
    for r in UserRole:
        if str(r.value).upper() == role_input:
            return r
    return UserRole.VIEWER


@router.get("", response_model=List[UserResponse])
async def list_users(
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(require_roles(["ADMIN"]))
):
    stmt = select(User).order_by(User.created_at.desc())
    result = await db.execute(stmt)
    users = result.scalars().all()

    response = []
    for u in users:
        role_val = u.role.value if hasattr(u.role, "value") else str(u.role)
        response.append({
            "id": str(u.id),
            "username": u.username,
            "email": u.email,
            "role": role_val.upper(),
            "full_name": u.full_name,
            "is_active": u.is_active,
            "last_login": u.last_login.isoformat() if u.last_login else None
        })
    return response


@router.post("", response_model=UserResponse)
async def create_user(
    user_in: UserCreate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(require_roles(["ADMIN"]))
):
    stmt = select(User).where((User.username == user_in.username) | (User.email == user_in.email))
    result = await db.execute(stmt)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El nombre de usuario o email ya está registrado"
        )

    target_role = parse_user_role(user_in.role)

    new_user = User(
        username=user_in.username,
        email=user_in.email,
        hashed_password=get_password_hash(user_in.password),
        role=target_role,
        full_name=user_in.full_name,
        is_active=True
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)

    role_str = new_user.role.value if hasattr(new_user.role, "value") else str(new_user.role)
    return {
        "id": str(new_user.id),
        "username": new_user.username,
        "email": new_user.email,
        "role": role_str.upper(),
        "full_name": new_user.full_name,
        "is_active": new_user.is_active,
        "last_login": None
    }


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    user_in: UserUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: TokenData = Depends(require_roles(["ADMIN"]))
):
    try:
        user_uuid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="ID de usuario inválido")

    stmt = select(User).where(User.id == user_uuid)
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if user_in.is_active is not None:
        user.is_active = user_in.is_active

    if user_in.role is not None:
        user.role = parse_user_role(user_in.role)

    await db.commit()
    await db.refresh(user)

    role_str = user.role.value if hasattr(user.role, "value") else str(user.role)
    return {
        "id": str(user.id),
        "username": user.username,
        "email": user.email,
        "role": role_str.upper(),
        "full_name": user.full_name,
        "is_active": user.is_active,
        "last_login": user.last_login.isoformat() if user.last_login else None
    }