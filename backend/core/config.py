from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    app_name: str = "ARIA"
    app_version: str = "1.0.0"
    environment: str = "development"
    secret_key: str = "change-me-in-production"
    frontend_url: str = "http://localhost:3000"
    backend_url: str = "http://localhost:8000"

    #N8N
    N8N_WEBHOOK_URL: Optional[str] = None  # O n8n_webhook_url: Optional[str] = None
    # LLMs
    google_api_key: str = ""
    openai_api_key: str = ""
    groq_api_key: str = ""

    # Ollama
    ollama_base_url: str = "http://192.168.64.1:11434"

    # Voice
    elevenlabs_api_key: str = ""
    elevenlabs_voice_id: str = "21m00Tcm4TlvDq8ikWAM"

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
    chunk_size: int = 1000
    chunk_overlap: int = 200
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