from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from core.config import settings


engine = create_async_engine(
    settings.database_url,
    echo=settings.environment == "development",
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# Migraciones ligeras que create_all no puede aplicar. Este proyecto no usa
# Alembic: el esquema se materializa con Base.metadata.create_all(), que crea
# tablas nuevas pero NUNCA altera una tabla ya existente. Cada vez que se añade
# una columna a un modelo ya desplegado hay que reflejarla aquí con una
# sentencia idempotente (ADD COLUMN IF NOT EXISTS), que se ejecuta en cada
# arranque justo después de create_all y es un no-op cuando la columna ya está.
_LIGHTWEIGHT_MIGRATIONS = [
    # message_index: añadida para agrupar las citas RAG por mensaje del asistente
    # al recargar una conversación (ver RagReference.message_index).
    "ALTER TABLE rag_references ADD COLUMN IF NOT EXISTS message_index INTEGER",
    # notification_status / notification_sent_at: resultado persistido del envío
    # de la alerta a n8n/Telegram (ver Incident y _send_n8n_notification).
    "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS notification_status VARCHAR(10)",
    "ALTER TABLE incidents ADD COLUMN IF NOT EXISTS notification_sent_at TIMESTAMP",
]


async def create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for statement in _LIGHTWEIGHT_MIGRATIONS:
            await conn.execute(text(statement))
