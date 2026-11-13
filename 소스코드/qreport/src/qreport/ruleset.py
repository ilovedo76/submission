from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields, replace as _replace


@dataclass(frozen=True)
class RuleSet:
    ucl_sigma: float = 3
    min_count: int = 3
    run_length: int = 9
    surge_ratio: float = 1.5
    surge_min: int = 8
    alpha: float = 0.05
    overdispersion_sigma_z: float = 1.5
    level_rise_ratio: float = 1.5
    heatmap_basis: str = "max"
    seg_months: tuple = (2, 4, 6)
    prior_months: int = 12

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> "RuleSet":
        d = json.loads(s)
        names = {f.name for f in fields(cls)}
        d = {k: v for k, v in d.items() if k in names}
        if "seg_months" in d:
            d["seg_months"] = tuple(d["seg_months"])
        return cls(**d)

    def replace(self, **kw) -> "RuleSet":
        if "seg_months" in kw:
            kw["seg_months"] = tuple(kw["seg_months"])
        return _replace(self, **kw)


DEFAULT_RULES = RuleSet()
