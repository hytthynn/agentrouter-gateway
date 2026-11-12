"""Стратегии маршрутизации (FR-6.3): round_robin / least_used / sequential. Состояние — в памяти."""

import itertools

from gateway_app.db import orm


class RoutingStrategy:
    """Порядок кандидатов; счётчики токенов — in-memory (инициализация из БД при рестарте)."""

    def __init__(self, name: str = "round_robin") -> None:
        self.name = name
        self._rr = itertools.count()
        self._tokens: dict[int, int] = {}

    def configure(self, name: str) -> None:
        self.name = name

    def seed_tokens(self, account_id: int, tokens_used: int) -> None:
        self._tokens[account_id] = tokens_used

    def add_usage(self, account_id: int, tokens: int) -> None:
        self._tokens[account_id] = self._tokens.get(account_id, 0) + tokens

    def order(self, accounts: list[orm.Account]) -> list[orm.Account]:
        if not accounts:
            return []
        if self.name == "sequential":
            return sorted(accounts, key=lambda a: a.id or 0)
        if self.name == "least_used":
            return sorted(accounts, key=lambda a: self._tokens.get(a.id or 0, 0))
        # round_robin: вращаем стартовый индекс, сохраняя порядок probation-приоритета
        n = len(accounts)
        start = next(self._rr) % n
        return accounts[start:] + accounts[:start]


router_state = RoutingStrategy()
