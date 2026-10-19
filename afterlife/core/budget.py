"""A spend meter for metered APIs.

OpenAlex prices every request (the response carries `meta.cost_usd`) and caps
each key, or each IP without a key, at a daily dollar budget. A fetch that
ignores that cap fails halfway through with a 429 and leaves a partial result
that looks complete. This meter makes the stop deliberate: the fetcher checks
it before each call, records what it spent, and lands the result marked
incomplete with the cursor needed to resume.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class BudgetExhausted(RuntimeError):
    pass


@dataclass
class Budget:
    limit_usd: float
    spent_usd: float = 0.0
    calls: int = 0
    _ledger: list[float] = field(default_factory=list, repr=False)

    def can_afford(self, estimate_usd: float) -> bool:
        return self.spent_usd + estimate_usd <= self.limit_usd + 1e-12

    def charge(self, cost_usd: float) -> None:
        if cost_usd < 0:
            raise ValueError("cost cannot be negative")
        self.spent_usd += cost_usd
        self.calls += 1
        self._ledger.append(cost_usd)

    def require(self, estimate_usd: float) -> None:
        if not self.can_afford(estimate_usd):
            raise BudgetExhausted(
                f"next call (~${estimate_usd:.4f}) would exceed ${self.limit_usd:.4f}; "
                f"spent ${self.spent_usd:.4f} over {self.calls} calls"
            )

    @property
    def last_cost(self) -> float:
        return self._ledger[-1] if self._ledger else 0.0

    def summary(self) -> dict:
        return {
            "limit_usd": round(self.limit_usd, 6),
            "spent_usd": round(self.spent_usd, 6),
            "calls": self.calls,
        }
