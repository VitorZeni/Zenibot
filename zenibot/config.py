"""Configuração via variáveis de ambiente.

Nenhum segredo é literal no código-fonte: tudo vem do ambiente ou do .env
(que está no .gitignore).
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="ZENIBOT_",
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    token: str = Field(min_length=50)
    # NoDecode: sem isto o pydantic-settings tentaria json.loads("111,222")
    # antes do validador abaixo rodar.
    owner_ids: Annotated[set[int], NoDecode] = Field(default_factory=set)
    dev_guild_id: int | None = None
    db_path: Path = Path("data/zenibot.db")
    log_level: str = "INFO"

    @field_validator("owner_ids", mode="before")
    @classmethod
    def _parse_owner_ids(cls, v: object) -> object:
        if isinstance(v, str):
            return {int(p) for p in v.replace(" ", "").split(",") if p}
        return v

    @field_validator("dev_guild_id", mode="before")
    @classmethod
    def _empty_to_none(cls, v: object) -> object:
        return None if v == "" else v

    @property
    def db_file(self) -> Path:
        """Caminho absoluto do banco, com o diretório pai já criado."""
        path = self.db_path if self.db_path.is_absolute() else ROOT / self.db_path
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


def load_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # campos vêm do ambiente
