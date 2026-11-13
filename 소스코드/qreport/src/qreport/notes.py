"""Engineer-written report fields (chapters 8 and 9), shared by the store, the app and the renderers."""
from __future__ import annotations

from dataclasses import dataclass, field

PLAN_KEYS = ("담당", "일정", "대책")


@dataclass
class Notes:
    issues: str = ""
    plan: list[dict] = field(default_factory=list)   # items: {"담당", "일정", "대책"}
