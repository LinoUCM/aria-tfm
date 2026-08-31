import asyncio
from sqlalchemy import select
from core.database import engine, AsyncSessionLocal
from models.database import Base, User
from core.security import get_password_hash

async def init_database():
    print("⏳ Generando tablas en PostgreSQL...")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✅ Tablas creadas.")

    async with AsyncSessionLocal() as session:
        stmt = select(User).where(User.username == "admin_aria")
        result = await session.execute(stmt)
        user = result.scalar_one_or_none()

        if not user:
            admin = User(
                username="admin_aria",
                email="admin@aria.local",
                full_name="Administrador ARIA",
                hashed_password=get_password_hash("Password123!"),
                role="admin",
                is_active=True,
                is_verified=True
            )
            session.add(admin)
            await session.commit()
            print("✅ Usuario 'admin_aria' registrado correctamente.")
        else:
            print("ℹ️ El usuario 'admin_aria' ya existe.")

if __name__ == "__main__":
    asyncio.run(init_database())