import os
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )

    # PostgreSQL Configuration
    POSTGRES_USER: str = Field(default="postgres")
    POSTGRES_PASSWORD: str = Field(default="postgres")
    POSTGRES_DB: str = Field(default="financial_rag")
    POSTGRES_HOST: str = Field(default="localhost")
    POSTGRES_PORT: int = Field(default=5432)

    # Model Settings
    EMBEDDING_MODEL_NAME: str = Field(default="BAAI/bge-base-en-v1.5")
    CROSS_ENCODER_MODEL_NAME: str = Field(default="cross-encoder/ms-marco-MiniLM-L-6-v2")
    EMBEDDING_DIMENSION: int = Field(default=768)

    # Connection Pool Settings
    DB_POOL_MIN: int = Field(default=1)
    DB_POOL_MAX: int = Field(default=20)

    # AWS S3 & SQS Configuration
    AWS_REGION: str = Field(default="us-east-1")
    AWS_S3_BUCKET: str = Field(default="financial-rag-documents")
    AWS_SQS_QUEUE_URL: str = Field(default="https://sqs.us-east-1.amazonaws.com/123456789012/financial-ingestion-queue")
    AWS_ACCESS_KEY_ID: str = Field(default="")
    AWS_SECRET_ACCESS_KEY: str = Field(default="")
    S3_ENDPOINT_URL: str | None = Field(default=None)  # Optional custom S3 endpoint for MinIO/LocalStack


    @property
    def DATABASE_URL(self) -> str:
        return f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

settings = Settings()
