from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel

from core.database import get_db
from models.database import User
from core.security import (
    create_access_token,
    verify_password,
    get_current_user_data,
    TokenData
)

router = APIRouter(prefix="/api/v1/auth", tags=["Autenticación"])


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict


@router.post("/login", response_model=TokenResponse)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db)
):
    # Buscar usuario en PostgreSQL por username o email
    stmt = select(User).where(
        (User.username == form_data.username) | (User.email == form_data.username)
    )
    result = await db.execute(stmt)
    user = result.scalar_one_or_none()

    # Validar existencia y contraseña
    if not user or not user.is_active or not verify_password(form_data.password, user.hashed_password):
        if user:
            user.failed_login_attempts += 1
            await db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales incorrectas o usuario desactivado"
        )

    # Actualizar auditoría con hora naive UTC
    user.last_login = datetime.utcnow()
    user.failed_login_attempts = 0
    await db.commit()

    # Obtener string del rol (si viene como Enum o str)
    role_str = user.role.value if hasattr(user.role, "value") else str(user.role)

    token_payload = {"sub": user.username, "role": role_str}
    token = create_access_token(token_payload)

    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "id": str(user.id),
            "username": user.username,
            "email": user.email,
            "full_name": user.full_name or user.username,
            "avatar_url": user.avatar_url,
            "role": role_str
        }
    }


@router.get("/me")
def get_me(current_user: TokenData = Depends(get_current_user_data)):
    return {"username": current_user.username, "role": current_user.role}