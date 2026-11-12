"""Схемы API (Pydantic v2) домена аккаунтов."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from gateway_app.db.orm import AccountStatus


class AccountCreateIn(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    base_url: str | None = None
    github_login: str = Field(min_length=1, max_length=100)
    github_password: str = Field(min_length=1, max_length=200)
    totp_secret: str = Field(min_length=8, max_length=200)
    system_token: str = Field(min_length=8, max_length=500)


class AccountPatchIn(BaseModel):
    name: str | None = Field(default=None, max_length=100)
    base_url: str | None = None
    enabled: bool | None = None


class AccountOut(BaseModel):
    id: int
    name: str
    base_url: str
    github_login: str
    provider_user_id: int | None
    status: AccountStatus
    group_name: str | None
    balance_quota: int | None
    used_quota: int | None
    balance_usd: float | None
    last_balance_at: datetime | None
    last_checkin_at: datetime | None
    last_checkin_reward_usd: float | None
    error_message: str | None
    created_at: datetime


class DeleteResult(BaseModel):
    deleted: bool
    provider_keys_deleted: int
    errors: list[str] = Field(default_factory=list)


class ReloginAccepted(BaseModel):
    task_id: str
    state: Literal["running"] = "running"
