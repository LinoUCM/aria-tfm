from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # App
    app_name: str = "ARIA"
    app_version: str = "1.0.0"
    environment: str = "development"
    secret_key: str = "change-me-in-production"
    frontend_url: str = "http://localhost:3000"
    backend_url: str = "http://localhost:8000"

    # LLMs
    google_api_key: str = ""
    openai_api_key: str = ""
    groq_api_key: str = ""

    # Voice
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"  # Rachel - default

    # Search
    tavily_api_key: str = ""

    # GitHub
    github_token: str = ""

    # Database
    database_url: str = "postgresql+asyncpg://aria:aria@localhost:5432/aria_db"

    # Redis
    redis_url: str = "redis://localhost:6379"

    # ChromaDB
    chroma_host: str = "localhost"
    chroma_port: int = 8001
    chroma_collection: str = "aria_knowledge_base"

    # Storage
    r2_account_id: Optional[str] = None
    r2_access_key_id: Optional[str] = None
    r2_secret_access_key: Optional[str] = None
    r2_bucket_name: str = "aria-uploads"

    # Datadog Simulator
    datadog_webhook_secret: str = "aria_webhook_secret_2024"

    # RAG Settings
    chunk_size: int = 2000
    chunk_overlap: int = 400
    rag_top_k: int = 5
    rag_score_threshold: float = 0.7

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def uploads_dir(self) -> str:
        return "/app/uploads" if self.is_production else "./uploads"

    class Config:
        env_file = ".env"
        case_sensitive = False


settings = Settings()
