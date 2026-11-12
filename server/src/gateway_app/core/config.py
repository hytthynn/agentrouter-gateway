"""Конфигурация приложения (pydantic-settings, 12-factor, .env)."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_data_dir() -> Path:
    """<корень репозитория>/data — определяется относительно пакета (server/src/gateway_app)."""
    pkg = Path(__file__).resolve()
    for parent in pkg.parents:
        if parent.name == "server" and (parent / "pyproject.toml").exists():
            return parent.parent / "data"
    return Path.cwd() / "data"


class Config(BaseSettings):
    """Настройки окружения. Всё перекрывается таблицей `settings` в БД (runtime)."""

    model_config = SettingsConfigDict(
        env_prefix="AGW_",
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "127.0.0.1"
    port: int = 8787
    log_level: str = "INFO"

    data_dir: Path = _default_data_dir()
    web_dist: Path | None = None  # по умолчанию <repo>/web/dist

    master_key: str | None = None  # иначе файл data/.master_key

    default_base_url: str = "https://agentrouter.org"
    default_port_api: int = 8787  # порт шлюза, показываемый в UI-сниппетах

    # ---- производные пути ----
    @property
    def db_path(self) -> Path:
        return self.data_dir / "app.db"

    @property
    def db_url_async(self) -> str:
        return f"sqlite+aiosqlite:///{self.db_path.as_posix()}"

    @property
    def db_url_sync(self) -> str:
        return f"sqlite:///{self.db_path.as_posix()}"

    @property
    def master_key_file(self) -> Path:
        return self.data_dir / ".master_key"

    @property
    def states_dir(self) -> Path:
        return self.data_dir / "states"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def app_log_file(self) -> Path:
        return self.logs_dir / "app.log"

    def default_web_dist(self) -> Path:
        if self.web_dist:
            return self.web_dist
        pkg = Path(__file__).resolve()
        for parent in pkg.parents:
            if parent.name == "server" and (parent / "pyproject.toml").exists():
                return parent.parent / "web" / "dist"
        return Path.cwd() / "web" / "dist"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.states_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_config() -> Config:
    cfg = Config()
    cfg.ensure_dirs()
    return cfg
