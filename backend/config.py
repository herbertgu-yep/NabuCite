import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, SecretStr
from sqlalchemy import URL

load_dotenv(Path(__file__).with_name(".env"))


class ModelSettings(BaseModel):
    base_url: str
    api_key: SecretStr
    llm_model: str
    embedding_model: str
    rerank_model: str


model = ModelSettings(
    base_url=os.getenv(
        "MODEL_BASE_URL", "https://your-model-provider.example/v1"
    ),
    api_key=SecretStr(os.getenv("MODEL_API_KEY", "").strip()),
    llm_model=os.getenv("LLM_MODEL", "qwen3.7-plus"),
    embedding_model=os.getenv("EMBEDDING_MODEL", "qwen3.7-text-embedding-flash"),
    rerank_model=os.getenv("RERANK_MODEL", "qwen3.7-text-rerank"),
)


def database_url() -> URL:
    password = os.getenv("POSTGRES_PASSWORD")
    if not password:
        raise RuntimeError("请配置 POSTGRES_PASSWORD。")
    return URL.create(
        "postgresql+asyncpg",
        username=os.getenv("POSTGRES_USER", "nabucite"),
        password=password,
        host=os.getenv("POSTGRES_HOST", "127.0.0.1"),
        port=int(os.getenv("POSTGRES_PORT", "55432")),
        database=os.getenv("POSTGRES_DB", "nabucite"),
    )
