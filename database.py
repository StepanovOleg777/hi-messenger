import os
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

# Используем переменную окружения или fallback на SQLite
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./messenger.db")

# Для PostgreSQL нужно заменить драйвер
if DATABASE_URL.startswith("postgresql"):
    DATABASE_URL = DATABASE_URL.replace("postgresql", "postgresql+asyncpg")

engine = create_async_engine(
    DATABASE_URL,
    echo=True,
    future=True
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False
)

async def get_db():
    async with AsyncSessionLocal() as session:
        yield session