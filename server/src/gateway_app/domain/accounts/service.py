"""Сервис аккаунтов: CRUD, фоновые flow добавления/перелогина, обновление баланса (FR-1)."""

import json
from typing import Any

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from gateway_app.automation.tasks import AutomationTask
from gateway_app.core.errors import (
    AutomationError,
    ConflictError,
    NotFoundError,
    ProviderAuthError,
    ProviderError,
)
from gateway_app.core.logging import get_logger
from gateway_app.core.security import SecretBox, get_secret_box
from gateway_app.db import orm
from gateway_app.db.orm import utcnow
from gateway_app.domain.accounts.base import (
    account_out,
    creds_of,
    get_account,
    github_creds_of,
    list_accounts,
)
from gateway_app.domain.catalog.models_sync import sync_account_models
from gateway_app.domain.keys.provider_keys import create_auto_key
from gateway_app.domain.logs.service import log_system
from gateway_app.providers.agentrouter.adapter import quota_to_usd
from gateway_app.providers.base import ProviderAdapter, ProviderCredentials

log = get_logger(__name__)

__all__ = [
    "account_out",
    "add_account_flow",
    "create_account_row",
    "creds_of",
    "delete_account",
    "get_account",
    "github_creds_of",
    "list_accounts",
    "patch_account",
    "refresh_all_balances",
    "refresh_balance",
    "refresh_balance_by_id",
    "relogin_flow",
]


# ---------------------------------------------------------------- CRUD

async def create_account_row(
    session: AsyncSession,
    box: SecretBox,
    *,
    name: str | None,
    base_url: str,
    github_login: str,
    github_password: str,
    totp_secret: str,
    system_token: str,
    provider_user_id: int | None,
    username: str | None,
    group_name: str | None,
    session_cookie: str | None,
    storage_state: dict | None,
) -> orm.Account:
    """Создать аккаунт status=active (шаг 6 сценария FR-1.2)."""
    dup = (
        await session.exec(
            select(orm.Account).where(orm.Account.github_login == github_login)  # type: ignore[attr-defined]
        )
    ).first()
    if dup is not None:
        raise ConflictError(f"Аккаунт с GitHub-логином {github_login!r} уже добавлен")

    row = orm.Account(
        name=name or username or github_login,
        base_url=base_url,
        github_login=github_login,
        github_password_enc=box.encrypt("github_password_enc", github_password),
        totp_secret_enc=box.encrypt("totp_secret_enc", totp_secret),
        system_token_enc=box.encrypt("system_token_enc", system_token),
        provider_user_id=provider_user_id,
        status=orm.AccountStatus.active,
        group_name=group_name,
    )
    if storage_state or session_cookie:
        payload: dict[str, Any] = {
            "storage_state": storage_state,
            "session_cookie": session_cookie,
        }
        row.session_enc = box.encrypt("session_enc", json.dumps(payload))
    session.add(row)
    await session.commit()
    await session.refresh(row)
    await log_system(
        session, "info", "oauth", f"Аккаунт {row.name} добавлен (user_id={provider_user_id})",
        account_id=row.id,
    )
    return row


async def patch_account(
    session: AsyncSession, account_id: int, *, name: str | None, base_url: str | None, enabled: bool | None
) -> orm.Account:
    row = await get_account(session, account_id)
    if name is not None:
        row.name = name
    if base_url is not None:
        row.base_url = base_url.rstrip("/")
    if enabled is not None:
        row.status = orm.AccountStatus.active if enabled else orm.AccountStatus.disabled
        row.error_message = None
    row.updated_at = utcnow()
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


async def delete_account(
    session: AsyncSession,
    adapter: ProviderAdapter,
    box: SecretBox,
    account_id: int,
    *,
    purge_provider_keys: bool,
) -> tuple[int, list[str]]:
    """Удалить аккаунт; опционально удалить ключи у провайдера (FR-1.4). Каскад локальный."""
    row = await get_account(session, account_id)
    deleted_remote = 0
    errors: list[str] = []
    if purge_provider_keys:
        keys = (
            await session.exec(
                select(orm.ProviderKey).where(orm.ProviderKey.account_id == account_id)  # type: ignore[attr-defined]
            )
        ).all()
        creds = creds_of(box, row)
        for k in keys:
            if k.provider_token_id is None:
                continue
            try:
                await adapter.delete_token(creds, k.provider_token_id)
                deleted_remote += 1
            except Exception as exc:
                errors.append(f"Ключ {k.key_masked}: {exc}")
    await session.delete(row)
    await session.commit()
    await log_system(session, "info", "oauth", f"Аккаунт {row.name} удалён", account_id=None)
    return deleted_remote, errors


# --------------------------------------------------------- фоновые flow

async def add_account_flow(
    task: AutomationTask,
    session_factory: Any,
    adapter: ProviderAdapter,
    data: dict[str, Any],
    *,
    default_base_url: str,
) -> dict[str, Any]:
    """Сценарий FR-1.2: OAuth → валидация токена → авто-ключ → синк моделей → сохранение."""
    from gateway_app.automation.github_login import github_oauth_login
    from gateway_app.core.config import get_config

    box = get_secret_box()
    base_url = (data.get("base_url") or default_base_url).rstrip("/")
    states_dir = get_config().states_dir

    # 1-2. GitHub OAuth → user_id
    task.add_step("oauth", "GitHub OAuth-вход")
    login_result = await github_oauth_login(
        ctx=task,
        base_url=base_url,
        github_login=data["github_login"],
        github_password=data["github_password"],
        totp_secret=data["totp_secret"],
        storage_state=None,
        screenshots_dir=states_dir / "pending",
    )

    # 3. Валидация system token
    task.add_step("token", "Проверка system access token")
    creds = ProviderCredentials(
        base_url=base_url,
        system_token=data["system_token"],
        provider_user_id=login_result["provider_user_id"],
    )
    try:
        self_info = await adapter.get_user_self(creds)
    except ProviderAuthError as exc:
        task.step_status("token", "error")
        raise AutomationError(f"System token отклонён провайдером: {exc}") from exc
    except ProviderError as exc:
        task.step_status("token", "error")
        raise AutomationError(f"Провайдер недоступен: {exc}") from exc
    if (
        self_info.id is not None
        and login_result["provider_user_id"] is not None
        and self_info.id != login_result["provider_user_id"]
    ):
        task.step_status("token", "error")
        raise AutomationError(
            f"user_id не совпадает: OAuth {login_result['provider_user_id']} ≠ token {self_info.id}"
        )
    task.step_status("token", "done")
    task.log(
        f"✓ Баланс аккаунта: {quota_to_usd(self_info.quota)}$ "
        f"(quota={self_info.quota}), группа={self_info.group or 'default'}"
    )

    # 4. Авто-ключ (FR-2.1) — создание аккаунт-строки здесь, чтобы ключ привязать
    task.add_step("account", "Создание аккаунта в шлюзе")
    session = session_factory()
    try:
        account = await create_account_row(
            session,
            box,
            name=data.get("name"),
            base_url=base_url,
            github_login=data["github_login"],
            github_password=data["github_password"],
            totp_secret=data["totp_secret"],
            system_token=data["system_token"],
            provider_user_id=login_result["provider_user_id"] or self_info.id,
            username=login_result["username"] or self_info.username,
            group_name=self_info.group,
            session_cookie=login_result.get("session_cookie"),
            storage_state=login_result.get("storage_state"),
        )
    finally:
        await session.close()
    task.step_status("account", "done")

    task.add_step("key", "Выпуск безлимитного ключа провайдера")
    session = session_factory()
    try:
        pk = await create_auto_key(session, adapter, box, account)
        task.log(f"✓ Ключ создан: {pk.key_masked} (безлимит, бессрочно, все модели)")
    finally:
        await session.close()
    task.step_status("key", "done")

    # 5. Синк моделей
    task.add_step("models", "Синхронизация каталога моделей")
    session = session_factory()
    try:
        count = await sync_account_models(session, adapter, box, account)
        task.log(f"✓ Моделей в каталоге после синка: {count}")
    finally:
        await session.close()
    task.step_status("models", "done")

    return {"account_id": account.id, "name": account.name}


async def relogin_flow(
    task: AutomationTask, session_factory: Any, adapter: ProviderAdapter, account_id: int
) -> dict[str, Any]:
    """Перелогин аккаунта (FR-1.5, §2.3): OAuth через сохранённые креды → новая сессия."""
    from gateway_app.automation.github_login import github_oauth_login
    from gateway_app.core.config import get_config

    box = get_secret_box()
    session = session_factory()
    try:
        account = await session.get(orm.Account, account_id)
        if account is None:
            raise NotFoundError(f"Аккаунт {account_id} не найден")
        login, password, totp = github_creds_of(box, account)
        storage: dict | None = None
        if account.session_enc:
            try:
                storage = (json.loads(box.decrypt("session_enc", account.session_enc) or "{}")).get(
                    "storage_state"
                )
            except Exception:
                storage = None

        task.add_step("oauth", "GitHub OAuth-перелогин")
        result = await github_oauth_login(
            ctx=task,
            base_url=account.base_url,
            github_login=login,
            github_password=password,
            totp_secret=totp,
            storage_state=storage,
            screenshots_dir=get_config().states_dir / str(account_id),
        )

        account.provider_user_id = result["provider_user_id"] or account.provider_user_id
        payload = json.dumps(
            {
                "storage_state": result.get("storage_state"),
                "session_cookie": result.get("session_cookie"),
            }
        )
        account.session_enc = box.encrypt("session_enc", payload)
        account.status = orm.AccountStatus.active
        account.error_message = None
        account.updated_at = utcnow()
        session.add(account)
        await session.commit()
        await log_system(session, "info", "oauth", f"Перелогин {account.name} успешен", account_id=account.id)
        return {"account_id": account.id}
    finally:
        await session.close()


# -------------------------------------------------------------- баланс

async def refresh_balance(
    session: AsyncSession, adapter: ProviderAdapter, box: SecretBox, account: orm.Account
) -> orm.Account:
    """Обновить баланс/группу из /api/user/self (FR-3.1). 401 → needs_relogin (FR-1.5)."""
    try:
        info = await adapter.get_user_self(creds_of(box, account))
    except ProviderAuthError as exc:
        account.status = orm.AccountStatus.needs_relogin
        account.error_message = str(exc)[:300]
        account.updated_at = utcnow()
        session.add(account)
        await session.commit()
        await log_system(session, "warn", "balance", f"{account.name}: токен отклонён → needs_relogin", account_id=account.id)
        return account
    account.balance_quota = info.quota
    account.used_quota = info.used_quota
    account.balance_usd = quota_to_usd(info.quota)
    account.group_name = info.group
    account.last_balance_at = utcnow()
    if account.status is orm.AccountStatus.needs_relogin:
        account.status = orm.AccountStatus.active
        account.error_message = None
    account.updated_at = utcnow()
    session.add(account)
    await session.commit()
    await session.refresh(account)
    return account


async def refresh_balance_by_id(session: AsyncSession, adapter: ProviderAdapter, box: SecretBox, account_id: int) -> orm.Account:
    account = await get_account(session, account_id)
    return await refresh_balance(session, adapter, box, account)


async def refresh_all_balances(session: AsyncSession, adapter: ProviderAdapter, box: SecretBox) -> int:
    """Джоба balance_refresh (§11): /api/user/self всех активных."""
    accounts = await list_accounts(session)
    ok = 0
    for acc in accounts:
        if acc.status not in (orm.AccountStatus.active, orm.AccountStatus.needs_relogin):
            continue
        try:
            await refresh_balance(session, adapter, box, acc)
            ok += 1
        except Exception as exc:
            await log_system(session, "error", "balance", f"{acc.name}: {exc}", account_id=acc.id)
    return ok
