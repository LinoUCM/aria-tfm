from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel
from enum import Enum

# Ajusta el import según dónde tengas tu archivo security.py (ej. core.security)
from core.security import (
    create_access_token,
    get_password_hash,
    verify_password,
    get_current_user_data,
    require_role,
    TokenData
)

router = APIRouter(prefix="/api/v1/auth", tags=["Autenticación"])

class UserRole(str, Enum):
    ADMIN = "admin"
    ON_CALL = "on_call"
    VIEWER = "viewer"

class UserRegister(BaseModel):
    username: str
    email: str
    password: str
    role: UserRole = UserRole.ON_CALL

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict

# Simulación de BD temporal en memoria
# Simulación de BD temporal en memoria con usuario por defecto
fake_users_db = {
    "admin_aria": {
        "username": "admin_aria",
        "email": "admin@aria.local",
        "hashed_password": get_password_hash("Password123!"),
        "role": "admin"
    }
}
@router.post("/register")
def register(user_in: UserRegister):
    if user_in.username in fake_users_db:
        raise HTTPException(status_code=400, detail="El usuario ya existe")
    
    hashed_pwd = get_password_hash(user_in.password)
    user_dict = {
        "username": user_in.username,
        "email": user_in.email,
        "hashed_password": hashed_pwd,
        "role": user_in.role.value
    }
    fake_users_db[user_in.username] = user_dict
    return {"message": "Usuario registrado exitosamente", "username": user_in.username, "role": user_in.role.value}

@router.post("/login", response_model=TokenResponse)
def login(form_data: OAuth2PasswordRequestForm = Depends()):
    user = fake_users_db.get(form_data.username)
    if not user or not verify_password(form_data.password, user["hashed_password"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales incorrectas"
        )
    
    token_payload = {"sub": user["username"], "role": user["role"]}
    token = create_access_token(token_payload)
    
    return {
        "access_token": token,
        "token_type": "bearer",
        "user": {
            "username": user["username"],
            "email": user["email"],
            "role": user["role"]
        }
    }

@router.get("/me")
def get_me(current_user: TokenData = Depends(get_current_user_data)):
    return {"username": current_user.username, "role": current_user.role}

@router.get("/verify-token")
def verify_token_for_n8n(current_user: TokenData = Depends(get_current_user_data)):
    return {
        "valid": True,
        "username": current_user.username,
        "role": current_user.role,
        "is_admin": current_user.role == "admin"
    }