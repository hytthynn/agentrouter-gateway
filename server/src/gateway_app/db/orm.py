"""ORM-модели (SQLModel) — схема §5.1 ТЗ. Все datetime — aware UTC."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Column, ForeignKey, Integer, String, UniqueConstraint
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    """Текущее время в UTC (aware)."""

    return datetime.now(UTC)


class AccountStatus(StrEnum):
    active = "active"
    needs_relogin = "needs_relogin"
    error = "error"
    disabled = "disabled"


class KeyStatus(StrEnum):
    active = "active"
    disabled = "disabled"


class KeyOrigin(StrEnum):
    auto = "auto"
    manual = "manual"


class RequestStatus(StrEnum):
    success = "success"
    error = "error"
    client_abort = "client_abort"


class SystemLogLevel(StrEnum):
    info = "info"
    warn = "warn"
    error = "error"


class SystemLogCategory(StrEnum):
    oauth = "oauth"
    checkin = "checkin"
    balance = "balance"
    sync = "sync"
    gateway = "gateway"
    scheduler = "scheduler"
    security = "security"


# ---------------------------------------------------------------- accounts
class Account(SQLModel, table=True):
    __tablename__ = "accounts"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    base_url: str = Field(default="https://agentrouter.org")

    github_login: str = Field(index=True)
    github_password_enc: bytes
    totp_secret_enc: bytes
    system_token_enc: bytes

    provider_user_id: int | None = None
    session_enc: bytes | None = None  # шифрованный storage-state (github + cookies AR)

    status: AccountStatus = Field(default=AccountStatus.active, index=True)
    group_name: str | None = None

    balance_quota: int | None = None
    used_quota: int | None = None
    balance_usd: float | None = None
    last_balance_at: datetime | None = None

    last_checkin_at: datetime | None = None
    last_checkin_reward_usd: float | None = None

    error_message: str | None = None

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


# ------------------------------------------------------------ provider_keys
class ProviderKey(SQLModel, table=True):
    __tablename__ = "provider_keys"

    id: int | None = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="accounts.id", index=True, ondelete="CASCADE")

    name: str
    key_enc: bytes  # полный sk-...
    key_masked: str

    provider_token_id: int | None = Field(default=None, index=True)
    unlimited_quota: bool = True
    remain_quota: int | None = None
    expired_time: int = -1  # unix-сек; -1 = бессрочно
    model_limits_enabled: bool = False
    model_limits: list[str] | None = Field(default=None, sa_column=Column(JSON))

    status: KeyStatus = Field(default=KeyStatus.active, index=True)
    created_via: KeyOrigin = Field(default=KeyOrigin.auto)

    used_quota: int | None = None  # расход по ключу (синк из /api/token/)

    created_at: datetime = Field(default_factory=utcnow)
    last_used_at: datetime | None = None


# ----------------------------------------------------------------- models
class ModelCatalog(SQLModel, table=True):
    __tablename__ = "models"

    model_id: str = Field(primary_key=True)
    enabled: bool = True
    added_from_account_id: int | None = Field(
        default=None,
        sa_column=Column(Integer, ForeignKey("accounts.id", ondelete="SET NULL"), nullable=True),
    )
    created_at: datetime = Field(default_factory=utcnow)


class AccountModel(SQLModel, table=True):
    __tablename__ = "account_models"
    __table_args__ = (UniqueConstraint("account_id", "model_id"),)

    id: int | None = Field(default=None, primary_key=True)
    account_id: int = Field(foreign_key="accounts.id", index=True, ondelete="CASCADE")
    model_id: str = Field(
        sa_column=Column(
            String, ForeignKey("models.model_id", ondelete="CASCADE"), index=True, nullable=False
        )
    )
    available: bool = True
    checked_at: datetime = Field(default_factory=utcnow)


# ------------------------------------------------------------ gateway_keys
class GatewayKey(SQLModel, table=True):
    __tablename__ = "gateway_keys"

    id: int | None = Field(default=None, primary_key=True)
    name: str

    key_enc: bytes
    key_hash: str = Field(unique=True, index=True)  # sha256 для быстрого поиска
    key_masked: str

    token_limit: int | None = None  # NULL = безлимит; иначе max total tokens
    tokens_used: int = 0
    request_count: int = 0

    allowed_models: list[str] | None = Field(default=None, sa_column=Column(JSON))
    expires_at: datetime | None = None

    status: KeyStatus = Field(default=KeyStatus.active, index=True)

    created_at: datetime = Field(default_factory=utcnow)
    last_used_at: datetime | None = None


# ------------------------------------------------------------ request_logs
class RequestLog(SQLModel, table=True):
    __tablename__ = "request_logs"

    id: int | None = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=utcnow, index=True)

    gateway_key_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer, ForeignKey("gateway_keys.id", ondelete="SET NULL"), index=True, nullable=True
        ),
    )  # лог переживает удаление ключа
    account_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer, ForeignKey("accounts.id", ondelete="SET NULL"), index=True, nullable=True
        ),
    )

    model: str = Field(index=True)
    stream: bool = False

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated: bool = False

    cost_quota: int | None = None
    cost_usd: float | None = None

    duration_ms: int = 0
    first_token_ms: int | None = None

    status: RequestStatus = Field(default=RequestStatus.success, index=True)
    http_status: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    attempts: list[dict[str, Any]] | None = Field(default=None, sa_column=Column(JSON))

    client_ua: str | None = None


# ------------------------------------------------------------- system_logs
class SystemLog(SQLModel, table=True):
    __tablename__ = "system_logs"

    id: int | None = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=utcnow, index=True)

    level: SystemLogLevel = Field(default=SystemLogLevel.info, index=True)
    category: SystemLogCategory = Field(default=SystemLogCategory.gateway, index=True)
    account_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer, ForeignKey("accounts.id", ondelete="SET NULL"), index=True, nullable=True
        ),
    )

    message: str
    details: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))


# --------------------------------------------------------------- settings
class Setting(SQLModel, table=True):
    __tablename__ = "settings"

    key: str = Field(primary_key=True)
    value: str


# --------------------------------------------------------- client_profiles
class ClientProfile(SQLModel, table=True):
    """Профили имперсонации для обхода WAF (§10.2) — seed при первом запуске."""

    __tablename__ = "client_profiles"

    name: str = Field(primary_key=True)
    user_agent: str
    extra_headers: dict[str, str] | None = Field(default=None, sa_column=Column(JSON))
    is_default: bool = False
