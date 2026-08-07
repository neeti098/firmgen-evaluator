from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore")

    openai_api_key: str = ""
    github_token: str = ""
    app_password: str = ""
    session_secret: str = "local-development-secret-change-me"

    openai_model: str = "gpt-5.6-sol"
    openai_adjudicator_model: str = "gpt-5.6-sol"
    openai_reasoning_effort: str = "low"
    max_concurrent_evaluations: int = Field(default=10, ge=1, le=10)
    adjudicate_low_confidence: bool = False
    adjudication_confidence_threshold: float = Field(default=0.65, ge=0, le=1)

    max_repo_archive_mb: int = Field(default=50, ge=1, le=250)
    max_repo_text_chars: int = Field(default=120_000, ge=20_000, le=500_000)
    max_media_mb: int = Field(default=200, ge=5, le=500)
    max_readme_images: int = Field(default=8, ge=0, le=30)
    max_video_frames: int = Field(default=16, ge=0, le=60)
    max_videos: int = Field(default=2, ge=0, le=5)

    mock_openai: bool = False
    data_dir: Path = Path("./data")

    @property
    def database_path(self) -> Path:
        return self.data_dir / "evaluator.db"

    @property
    def temp_dir(self) -> Path:
        return self.data_dir / "tmp"

    def prepare_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.temp_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.prepare_directories()
    return settings
