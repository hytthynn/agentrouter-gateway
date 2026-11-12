"""Чистые хелперы аккаунтов (без тяжёлых импортов) — разрывают циклы домена."""

from typing import Any

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from gateway_app.core.errors import NotFoundError
from gateway_app.core.security import SecretBox
from gateway_app.db import orm
from gateway_app.providers.base import ProviderCredentials


async def get_account(session: AsyncSession, account_id: int) -> orm.Account:
    row = await session.get(orm.Account, account_id)
    if row is None:
        raise NotFoundError(f"Аккаунт {account_id} не найден")
    return row


async def list_accounts(session: AsyncSession) -> list[orm.Account]:
    result = await session.exec(
        select(orm.Account).order_by(orm.Account.created_at.desc())  # type: ignore[attr-defined]
    )
    return list(result.all())


def creds_of(box: SecretBox, account: orm.Account) -> ProviderCredentials:
    """Расшифровать креды аккаунта для вызовов адаптера."""
    return ProviderCredentials(
        base_url=account.base_url,
        system_token=box.decrypt("system_token_enc", account.system_token_enc) or "",
        provider_user_id=account.provider_user_id,
    )


def github_creds_of(box: SecretBox, account: orm.Account) -> tuple[str, str, str]:
    return (
        account.github_login,
        box.decrypt("github_password_enc", account.github_password_enc) or "",
        box.decrypt("totp_secret_enc", account.totp_secret_enc) or "",
    )


def account_out(a: orm.Account) -> dict[str, Any]:
    return {
        "id": a.id,
        "name": a.name,
        "base_url": a.base_url,
        "github_login": a.github_login,
        "provider_user_id": a.provider_user_id,
        "status": a.status.value if isinstance(a.status, orm.AccountStatus) else a.status,
        "group_name": a.group_name,
        "balance_quota": a.balance_quota,
        "used_quota": a.used_quota,
        "balance_usd": a.balance_usd,
        "last_balance_at": a.last_balance_at,
        "last_checkin_at": a.last_checkin_at,
        "last_checkin_reward_usd": a.last_checkin_reward_usd,
        "error_message": a.error_message,
        "created_at": a.created_at,
    }
