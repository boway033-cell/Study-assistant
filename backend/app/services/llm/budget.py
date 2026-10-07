"""本机 AI 预算。估算用于提示；运行上限按请求正文持续检查。"""
from __future__ import annotations

import contextvars
import math
from dataclasses import dataclass


def estimate_tokens(chars: int) -> int:
    # 中文、英文及标点混排时故意偏保守；不是供应商账单 token。
    return math.ceil(max(0, chars) / 2)


class BudgetExceeded(ValueError):
    pass


@dataclass
class TaskBudget:
    max_tokens: int = 0
    max_calls: int = 0
    used_tokens: int = 0
    used_calls: int = 0
    output_chars: int = 0

    def remaining_tokens(self) -> int | None:
        """Estimated task headroom; None means the user configured no token limit."""
        return max(0, self.max_tokens - self.used_tokens) if self.max_tokens else None

    def start_call(self, input_chars: int) -> None:
        needed = estimate_tokens(input_chars)
        if self.max_calls and self.used_calls + 1 > self.max_calls:
            raise BudgetExceeded(f"本次 AI 任务已达到 {self.max_calls} 次调用上限")
        # Starting a generation with no room for even one output token wastes
        # a provider call and leaves no safe server-side completion cap.
        if self.max_tokens and self.used_tokens + needed >= self.max_tokens:
            raise BudgetExceeded(f"本次 AI 任务已达到 {self.max_tokens} 估算 Token 上限")
        self.used_calls += 1
        self.used_tokens += needed

    def output(self, chars: int) -> None:
        needed = estimate_tokens(self.output_chars + chars) - estimate_tokens(self.output_chars)
        if self.max_tokens and self.used_tokens + needed > self.max_tokens:
            raise BudgetExceeded(f"本次 AI 任务已达到 {self.max_tokens} 估算 Token 上限")
        self.output_chars += chars
        self.used_tokens += needed


current_budget: contextvars.ContextVar[TaskBudget | None] = contextvars.ContextVar(
    "current_ai_task_budget", default=None
)


def load_default_budget() -> TaskBudget:
    """所有 AI 路径共享的保底上限；研究页可用确认后的单任务上限覆盖。"""
    import json
    from backend.app.core.database import SessionLocal
    from backend.app.models import Setting

    with SessionLocal() as db:
        row = db.get(Setting, "ai_default_budget")
        try:
            value = json.loads(row.value) if row else {}
        except (ValueError, TypeError):
            value = {}
    if not isinstance(value, dict):
        value = {}
    return TaskBudget(max_tokens=max(0, int(value.get("max_tokens", 200000))),
                      max_calls=max(0, int(value.get("max_calls", 50))))
