"""Central settings for the whole suite, read once from .env."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


class Settings:
    database_url: str = os.environ.get(
        "DATABASE_URL", "postgresql+psycopg2://suite_user:change_me@localhost:5434/smb_ai_suite"
    )
    neon_database_url: str = os.environ.get("NEON_DATABASE_URL", "")

    llamacpp_embedding_url: str = os.environ.get("LLAMACPP_EMBEDDING_URL", "http://localhost:8090")
    llamacpp_generation_url: str = os.environ.get("LLAMACPP_GENERATION_URL", "http://localhost:8091")
    embedding_dim: int = int(os.environ.get("EMBEDDING_DIM", 768))

    max_critic_retries: int = int(os.environ.get("MAX_CRITIC_RETRIES", 2))
    chunk_size_tokens: int = int(os.environ.get("CHUNK_SIZE_TOKENS", 400))
    chunk_overlap_tokens: int = int(os.environ.get("CHUNK_OVERLAP_TOKENS", 60))
    top_k_retrieval: int = int(os.environ.get("TOP_K_RETRIEVAL", 6))

    n8n_webhook_url: str = os.environ.get("N8N_WEBHOOK_URL", "")


settings = Settings()