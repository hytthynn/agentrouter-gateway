"""Чек-ин (FR-8, §2.3): POST → re-login (нюанс зачисления) → дельта баланса → логи."""

import asyncio
import random
from typing import Any

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from gateway_app.automation.tasks import AutomationTask
from gateway_app.core.errors import ProviderAuthError, ProviderError
from gateway_app.core.logging import get_logger
from gateway_app.core.security import SecretBox
from gateway_app.db import orm
from gateway_app.db.database import make_session
from gateway_app.db.orm import utcnow
from gateway_app.domain.accounts.base import creds_of, get_account
from gateway_app.domain.accounts.service import relogin_flow
from gateway_app.domain.logs.service import log_system
from gateway_app.providers.agentrouter.adapter import quota_to_usd
from gateway_app.providers.base import ProviderAdapter

log = get_logger(__name__)

DEFAULT_REWARD_USD = 25.0  # §2.1
QUOTA_PER_UNIT = 500_000
CHECKIN_PATH_SETTING = "checkin_path_cache"


async def _cached_checkin_path(session: AsyncSession) -> str | None:
    row = await session.get(orm.Setting, CHECKIN_PATH_SETTING)
    return row.value if row and row.value else None


async def _cache_checkin_path(session: AsyncSession, path: str | None) -> None:
    row = await session.get(orm.Setting, CHECKIN_PATH_SETTING)
    if row is None:
        row = orm.Setting(key=CHECKIN_PATH_SETTING, value=path or "")
    else:
        row.value = path or ""
    session.add(row)
    await session.commit()


async def run_checkin(
    session: AsyncSession,
    adapter: ProviderAdapter,
    box: SecretBox,
    account: orm.Account,
    *,
    relogin_after: bool,
) -> dict[str, Any]:
    """FR-8.3: POST чек-ин → re-login → GET user/self → награда/дельта. «Уже» — info (не ошибка).

    Runtime-discovery (§2.6): форк AgentRouter не имеет POST-эндпоинта чек-ина — бонус
    засчитывается при самом входе («签到成功» на логине, FAQ: «нужно перелогиниться, чтобы
    зачислилось»). Если POST-кандидаты 404 → выполняем re-login как чек-ин и меряем дельту.
    """
    if account.status not in (orm.AccountStatus.active, orm.AccountStatus.needs_relogin):
        return {"status": "skipped", "reason": f"статус {account.status.value}"}

    # Уже чекинились сегодня (локальная дата) — не долбим провайдера и не перелогиниваемся
    if account.last_checkin_at is not None and account.last_checkin_at.date() == utcnow().date():
        await log_system(
            session, "info", "checkin",
            f"{account.name}: чек-ин сегодня уже выполнялся (в {account.last_checkin_at:%H:%M})",
            account_id=account.id,
        )
        return {"status": "already", "message": "чек-ин сегодня уже выполнялся"}

    creds = creds_of(box, account)

    # Баланс до (для дельты)
    before = None
    try:
        before = await adapter.get_user_self(creds)
    except Exception as exc:
        log.debug("Баланс до чек-ина не получен: %s", exc)

    cached_path = await _cached_checkin_path(session)
    try:
        outcome = await adapter.checkin(creds, cached_path=cached_path)
    except ProviderAuthError:
        account.status = orm.AccountStatus.needs_relogin
        account.error_message = "Токен отклонён при чек-ине (401)"
        account.updated_at = utcnow()
        session.add(account)
        await session.commit()
        await log_system(
            session, "error", "checkin", f"{account.name}: 401 при чек-ине", account_id=account.id
        )
        return {"status": "auth_error", "reason": "401"}
    except ProviderError as exc:
        if cached_path:
            await _cache_checkin_path(session, None)  # сброс кэша пути — перебор заново
        await log_system(
            session, "error", "checkin", f"{account.name}: чек-ин не удался: {exc}", account_id=account.id
        )
        return {"status": "error", "reason": str(exc)}

    if outcome.path and outcome.path != cached_path:
        await _cache_checkin_path(session, outcome.path)  # §2.6 п.3 — кэш найденного эндпоинта

    if outcome.unavailable:
        return await _checkin_via_login(session, adapter, box, account, before, relogin_after)

    if outcome.already_done:
        await log_system(
            session, "info", "checkin", f"{account.name}: чек-ин уже выполнен сегодня",
            account_id=account.id, details={"message": outcome.message},
        )
        return {"status": "already", "message": outcome.message}

    # §2.3: бонус зачисляется только после выхода/повторного входа
    if relogin_after and account.session_enc:
        try:
            task = AutomationTask(kind="relogin_after_checkin", account_id=account.id)
            await relogin_flow(task, make_session, adapter, account.id)  # type: ignore[arg-type]
        except Exception as exc:
            await log_system(
                session, "warn", "checkin",
                f"{account.name}: re-login после чек-ина не удался ({exc}); бонус может прийти позже",
                account_id=account.id,
            )
    elif relogin_after and not account.session_enc:
        await log_system(
            session, "info", "checkin",
            f"{account.name}: сессии нет — re-login пропущен; бонус зачислится при следующем перелогине",
            account_id=account.id,
        )

    # Баланс после + дельта
    delta_quota: int | None = None
    try:
        after = await adapter.get_user_self(creds_of(box, account))
        if before is not None and before.quota is not None and after.quota is not None:
            delta_quota = after.quota - before.quota
        account.balance_quota = after.quota
        account.used_quota = after.used_quota
        account.balance_usd = quota_to_usd(after.quota)
        account.last_balance_at = utcnow()
    except Exception as exc:
        await log_system(
            session, "warn", "checkin", f"{account.name}: баланс после чек-ина не прочитан: {exc}",
            account_id=account.id,
        )

    account.last_checkin_at = utcnow()
    if delta_quota is not None and delta_quota > 0:
        account.last_checkin_reward_usd = round(delta_quota / QUOTA_PER_UNIT, 2)
    elif delta_quota == 0:
        account.last_checkin_reward_usd = 0.0
        await log_system(
            session, "warn", "checkin",
            f"{account.name}: бонус не отразился на балансе после re-login — повтор на следующем запуске джобы",
            account_id=account.id,
        )
    else:
        account.last_checkin_reward_usd = None
    account.updated_at = utcnow()
    session.add(account)
    await session.commit()

    msg = f"{account.name}: чек-ин успешен"
    if delta_quota is not None:
        msg += f", дельта {delta_quota} quota (+${quota_to_usd(delta_quota)})"
    else:
        msg += f", ожидаемая награда ${DEFAULT_REWARD_USD}"
    await log_system(session, "info", "checkin", msg, account_id=account.id)
    return {
        "status": "success",
        "message": outcome.message,
        "delta_quota": delta_quota,
        "reward_usd": account.last_checkin_reward_usd,
    }


async def run_checkin_by_id(
    session: AsyncSession, adapter: ProviderAdapter, box: SecretBox, account_id: int, *, relogin_after: bool
) -> dict[str, Any]:
    account = await get_account(session, account_id)
    return await run_checkin(session, adapter, box, account, relogin_after=relogin_after)


async def _checkin_via_login(
    session: AsyncSession,
    adapter: ProviderAdapter,
    box: SecretBox,
    account: orm.Account,
    before: Any,
    relogin_after: bool,
) -> dict[str, Any]:
    """Механика форка: бонус зачисляется при самом входе (проверено по бандлу консоли).

    Делаем re-login — это и есть чек-ин; дельта баланса решает success/already.
    """
    if not relogin_after:
        msg = (
            "Чек-ин у провайдера засчитывается при входе (эндпоинт не реализован); "
            "включите «Re-login после чек-ина» в настройках"
        )
        await log_system(session, "info", "checkin", f"{account.name}: {msg}", account_id=account.id)
        return {"status": "unavailable", "message": msg}
    if not account.session_enc:
        msg = (
            "Чек-ин у провайдера засчитывается при входе; сессии нет — "
            "выполните «Перелогин» на карточке аккаунта"
        )
        await log_system(session, "info", "checkin", f"{account.name}: {msg}", account_id=account.id)
        return {"status": "unavailable", "message": msg}

    try:
        task = AutomationTask(kind="checkin_login", account_id=account.id)
        await relogin_flow(task, make_session, adapter, account.id)  # type: ignore[arg-type]
    except Exception as exc:
        await log_system(
            session, "warn", "checkin",
            f"{account.name}: перелогин (чек-ин через вход) не удался: {exc}",
            account_id=account.id,
        )
        return {"status": "error", "reason": f"перелогин не удался: {str(exc)[:200]}"}

    # Баланс после + дельта
    delta_quota: int | None = None
    try:
        after = await adapter.get_user_self(creds_of(box, account))
        if before is not None and before.quota is not None and after.quota is not None:
            delta_quota = after.quota - before.quota
        account.balance_quota = after.quota
        account.used_quota = after.used_quota
        account.balance_usd = quota_to_usd(after.quota)
        account.last_balance_at = utcnow()
    except Exception as exc:
        await log_system(
            session, "warn", "checkin", f"{account.name}: баланс после перелогина не прочитан: {exc}",
            account_id=account.id,
        )

    account.last_checkin_at = utcnow()
    account.last_checkin_reward_usd = (
        round(delta_quota / QUOTA_PER_UNIT, 2) if delta_quota is not None and delta_quota > 0 else 0.0
    )
    account.updated_at = utcnow()
    session.add(account)
    await session.commit()

    if delta_quota is not None and delta_quota > 0:
        msg = (
            f"{account.name}: чек-ин через вход успешен, "
            f"дельта {delta_quota} quota (+${quota_to_usd(delta_quota)})"
        )
        await log_system(session, "info", "checkin", msg, account_id=account.id)
        return {
            "status": "success",
            "message": "чек-ин зачтён при входе",
            "delta_quota": delta_quota,
            "reward_usd": account.last_checkin_reward_usd,
        }

    msg = (
        f"{account.name}: перелогин выполнен; бонус за сегодня уже был зачтён при входе "
        "(дельта 0) — следующий чек-ин при завтрашнем входе"
    )
    await log_system(session, "info", "checkin", msg, account_id=account.id)
    return {"status": "already", "message": "бонус за сегодня уже зачтён при входе"}


async def run_checkin_all(
    session: AsyncSession,
    adapter: ProviderAdapter,
    box: SecretBox,
    *,
    relogin_after: bool,
    parallel: int = 2,
    jitter_s: float = 0.0,
) -> list[dict[str, Any]]:
    """FR-8.2: по всем активным, параллелизм 2, случайный порядок; джиттер задаёт джоба."""
    accounts = (
        await session.exec(
            select(orm.Account).where(orm.Account.status == orm.AccountStatus.active)  # type: ignore[attr-defined]
        )
    ).all()
    order = list(accounts)
    random.shuffle(order)
    semaphore = asyncio.Semaphore(parallel)

    async def one(acc: orm.Account) -> dict[str, Any]:
        async with semaphore:
            if jitter_s > 0:
                await asyncio.sleep(random.uniform(0, jitter_s))
            return await run_checkin(session, adapter, box, acc, relogin_after=relogin_after)

    results = await asyncio.gather(*(one(a) for a in order))
    return [
        {"account_id": a.id, "name": a.name, **r} for a, r in zip(order, results, strict=True)
    ]
