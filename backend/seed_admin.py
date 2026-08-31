import asyncio
from sqlalchemy import select

# 1. Sesión de la base de datos (core/database.py)
try:
    from core.database import AsyncSessionLocal
    IS_ASYNC = True
except ImportError:
    from core.database import SessionLocal as AsyncSessionLocal
    IS_ASYNC = False

# 2. Modelo de usuario (models/database.py)
from models.database import User

# 3. Utilidad de hashing (core/security.py)
from core.security import get_password_hash


async def seed_admin_async():
    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.username == "admin_aria")
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            admin = User(
                username="admin_aria",
                email="admin@aria.local",
                hashed_password=get_password_hash("Password123!"),
                role="admin",
                is_active=True
            )
            session.add(admin)
            await session.commit()
            print("✅ Usuario 'admin_aria' creado exitosamente.")
        else:
            user.hashed_password = get_password_hash("Password123!")
            await session.commit()
            print("🔄 Contraseña del usuario 'admin_aria' actualizada a 'Password123!'.")


def seed_admin_sync():
    db = AsyncSessionLocal()
    try:
        user = db.query(User).filter(User.username == "admin_aria").first()
        if not user:
            admin = User(
                username="admin_aria",
                email="admin@aria.local",
                hashed_password=get_password_hash("Password123!"),
                role="admin",
                is_active=True
            )
            db.add(admin)
            db.commit()
            print("✅ Usuario 'admin_aria' creado exitosamente.")
        else:
            user.hashed_password = get_password_hash("Password123!")
            db.commit()
            print("🔄 Contraseña del usuario 'admin_aria' actualizada a 'Password123!'.")
    finally:
        db.close()


if __name__ == "__main__":
    if IS_ASYNC:
        asyncio.run(seed_admin_async())
    else:
        seed_admin_sync()