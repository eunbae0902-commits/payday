"""과거 시간봉 CSV로 동일 엔진을 돌려 성과를 검증."""
from __future__ import annotations

import csv
from collections import defaultdict
from datetime import datetime

from .broker import PaperBroker
from .config import Config
from .engine import Engine, new_state


def _ts(raw: str) -> float:
    try:
        return float(raw)
    except ValueError:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()


def load_csv(path: str) -> dict[float, dict[str, float]]:
    """CSV 열: timestamp,symbol,close (timestamp는 유닉스초 또는 ISO8601)."""
    rows: dict[float, dict[str, float]] = defaultdict(dict)
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows[_ts(r["timestamp"])][r["symbol"]] = float(r["close"])
    return dict(sorted(rows.items()))


def run(cfg: Config, rows: dict[float, dict[str, float]]) -> dict:
    half = cfg.spread_bps / 20_000
    state = new_state(cfg)
    eng = Engine(cfg, PaperBroker(), state)
    peak, mdd, eq, goal_ts = cfg.starting_capital, 0.0, cfg.starting_capital, None
    for ts, closes in rows.items():
        quotes = {s: (c * (1 - half), c * (1 + half)) for s, c in closes.items() if s in cfg.quote_symbols}
        if set(quotes) != set(cfg.quote_symbols):
            continue
        eq = eng.tick(ts, quotes)
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
        if state["halted"] == "GOAL" and goal_ts is None:
            goal_ts = ts
        if state["halted"]:
            break
    trades = [l for l in state["log"] if " SELL " in f" {l} "]
    wins = [l for l in trades if "PnL +" in l]
    return {
        "final_equity": eq,
        "return_pct": (eq / cfg.starting_capital - 1) * 100,
        "max_drawdown_pct": mdd * 100,
        "trades": len(trades),
        "win_rate_pct": (len(wins) / len(trades) * 100) if trades else 0.0,
        "halted": state["halted"],
        "goal_reached_at": goal_ts,
        "milestones_hit": state["milestones_hit"],
        "vault_cost": state["vault_cost"],
        "log": state["log"],
    }
