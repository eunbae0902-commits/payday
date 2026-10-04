"""과거 시간봉 CSV로 동일 엔진을 돌려 성과를 검증.

CSV의 timestamp는 시간봉의 '시작' 시각(업비트 candle_date_time_utc와 동일)이고 close는 그 봉의 종가.
4시간봉·일봉 설정이어도 손절선은 매 시간봉마다 확인한다.
"""
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


def run(cfg: Config, rows: dict[float, dict[str, float]],
        start_ts: float | None = None, end_ts: float | None = None) -> dict:
    """start_ts 이전 데이터는 지표 준비(warm-up)에만 쓰고 매매하지 않는다."""
    half = cfg.spread_bps / 20_000
    state = new_state(cfg)
    eng = Engine(cfg, PaperBroker(), state)
    peak, mdd, eq, goal_ts = cfg.starting_capital, 0.0, cfg.starting_capital, None
    first_px: dict[str, float] = {}
    last_px: dict[str, float] = {}
    for ts, closes in rows.items():
        if end_ts is not None and ts >= end_ts:
            break
        quotes = {s: (c * (1 - half), c * (1 + half)) for s, c in closes.items() if s in cfg.quote_symbols}
        if set(quotes) != set(cfg.quote_symbols):
            continue
        if start_ts is not None and ts < start_ts:
            for s in cfg.symbols:
                eng._record_bar(s, ts, closes[s])
            continue
        for s in cfg.symbols:
            first_px.setdefault(s, closes[s])
            last_px[s] = closes[s]
        bar_complete = int(ts + 3600) % (3600 * cfg.bar_hours) == 0
        eq = eng.tick(ts, quotes, bar_complete=bar_complete)
        peak = max(peak, eq)
        mdd = max(mdd, 1 - eq / peak)
        if state["halted"] == "GOAL" and goal_ts is None:
            goal_ts = ts
        if state["halted"]:
            break
    stats = state["stats"]
    hold = {s: (last_px[s] / first_px[s] - 1) * 100 for s in first_px}
    return {
        "final_equity": eq,
        "return_pct": (eq / cfg.starting_capital - 1) * 100,
        "max_drawdown_pct": mdd * 100,
        "trades": stats["trades"],
        "win_rate_pct": (stats["wins"] / stats["trades"] * 100) if stats["trades"] else 0.0,
        "buy_and_hold_pct": hold,         # 같은 기간 그냥 들고 있었을 때 수익률
        "halted": state["halted"],
        "goal_reached_at": goal_ts,
        "milestones_hit": state["milestones_hit"],
        "vault_cost": state["vault_cost"],
        "log": state["log"],
    }
