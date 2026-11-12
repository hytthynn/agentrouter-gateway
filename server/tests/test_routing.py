"""Тесты роутинга: стратегии (FR-6.3) и пул кандидатов (probation — в конец)."""

from gateway_app.db import database
from gateway_app.db.orm import Account, AccountModel, AccountStatus, ModelCatalog, utcnow
from gateway_app.domain.routing.pool import candidates_for_model
from gateway_app.domain.routing.strategy import RoutingStrategy


def _acc(id_):  # type: ignore[no-untyped-def]
    return Account(
        id=id_, name=f"acc{id_}", base_url="https://x", github_login=f"u{id_}",
        github_password_enc=b"x", totp_secret_enc=b"x", system_token_enc=b"x",
        status=AccountStatus.active, created_at=utcnow(),
    )


def test_round_robin_rotates():
    s = RoutingStrategy("round_robin")
    accounts = [_acc(1), _acc(2), _acc(3)]
    first = [a.id for a in s.order(accounts)]
    second = [a.id for a in s.order(accounts)]
    third = [a.id for a in s.order(accounts)]
    assert first[0] == 1 and second[0] == 2 and third[0] == 3
    assert sorted(first) == [1, 2, 3]


def test_sequential_stable():
    s = RoutingStrategy("sequential")
    accounts = [_acc(3), _acc(1), _acc(2)]
    assert [a.id for a in s.order(accounts)] == [1, 2, 3]


def test_least_used_prefers_low_tokens():
    s = RoutingStrategy("least_used")
    s.seed_tokens(1, 1000)
    s.seed_tokens(2, 10)
    s.seed_tokens(3, 100)
    accounts = [_acc(1), _acc(2), _acc(3)]
    assert [a.id for a in s.order(accounts)] == [2, 3, 1]
    s.add_usage(2, 5000)
    assert s.order(accounts)[0].id == 3


async def test_pool_filters_and_probation_last(seeded_db):
    async with database.make_session() as session:
        session.add(ModelCatalog(model_id="glm-4.6", enabled=True))
        for i, (status, group, balance) in enumerate(
            [
                (AccountStatus.active, "default", 1000),      # 1 — кандидат
                (AccountStatus.active, "probation", 2000),    # 2 — кандидат, в конец
                (AccountStatus.disabled, "default", 5000),    # 3 — исключён
                (AccountStatus.active, "default", 0),         # 4 — нулевой баланс
                (AccountStatus.active, "default", None),      # 5 — баланс неизвестен
            ],
            start=1,
        ):
            session.add(
                Account(
                    id=i, name=f"a{i}", base_url="https://x", github_login=f"u{i}",
                    github_password_enc=b"x", totp_secret_enc=b"x", system_token_enc=b"x",
                    status=status, group_name=group, balance_quota=balance, created_at=utcnow(),
                )
            )
        await session.flush()  # аккаунты до связей
        for i in (1, 2, 5):
            session.add(AccountModel(account_id=i, model_id="glm-4.6", available=True))
        await session.commit()

        candidates = await candidates_for_model(session, "glm-4.6")
        ids = [c.id for c in candidates]
        assert 3 not in ids and 4 not in ids
        assert set(ids) == {1, 2, 5}
        assert ids[-1] == 2  # probation в конец
