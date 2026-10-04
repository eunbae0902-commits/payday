"""펀드 운용 엔진: 한 번의 tick마다 시세 반영 → 리스크 점검 → 매매."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import strategy
from .config import Config

MAX_BARS = 1000


def new_state(cfg: Config) -> dict:
    return {
        "cash": cfg.starting_capital,
        "positions": {},          # symbol -> {qty, entry, stop, peak}
        "bars": {},               # symbol -> [hourly closes]
        "last_bar": {},           # symbol -> hour index
        "peak_equity": cfg.starting_capital,
        "day": None,
        "day_start_equity": cfg.starting_capital,
        "halted": None,           # None | "GOAL" | "DRAWDOWN"
        "log": [],
    }


def load_state(cfg: Config) -> dict:
    p = Path(cfg.state_dir) / f"state_{cfg.mode}.json"
    return json.loads(p.read_text()) if p.exists() else new_state(cfg)


def save_state(cfg: Config, state: dict) -> None:
    d = Path(cfg.state_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"state_{cfg.mode}.json").write_text(json.dumps(state, indent=2))


def equity(state: dict, quotes: dict[str, tuple[float, float]]) -> float:
    total = state["cash"]
    for sym, pos in state["positions"].items():
        total += pos["qty"] * quotes[sym][0]   # 매도호가(bid) 기준 평가
    return total


class Engine:
    def __init__(self, cfg: Config, broker, state: dict):
        self.cfg, self.broker, self.state = cfg, broker, state

    def _log(self, ts: float, msg: str) -> None:
        stamp = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M")
        self.state["log"].append(f"{stamp} {msg}")
        self.state["log"] = self.state["log"][-500:]

    def _record_bar(self, sym: str, ts: float, mid: float) -> None:
        hour = int(ts // 3600)
        bars = self.state["bars"].setdefault(sym, [])
        if self.state["last_bar"].get(sym) == hour and bars:
            bars[-1] = mid
        else:
            bars.append(mid)
            self.state["last_bar"][sym] = hour
        del bars[:-MAX_BARS]

    def _sell_all(self, ts: float, quotes, reason: str) -> None:
        for sym in list(self.state["positions"]):
            self._exit(sym, ts, quotes[sym][0], reason)

    def _exit(self, sym: str, ts: float, bid: float, reason: str) -> None:
        pos = self.state["positions"][sym]
        filled = self.broker.sell(self.state, sym, pos["qty"], bid)
        if filled:
            pnl = (bid - pos["entry"]) * filled
            self._log(ts, f"SELL {sym} {filled:.8f} @ {bid:,.2f} ({reason}) PnL {pnl:+,.2f}")
            del self.state["positions"][sym]

    def tick(self, ts: float, quotes: dict[str, tuple[float, float]]) -> float:
        cfg, st = self.cfg, self.state
        for sym, (bid, ask) in quotes.items():
            self._record_bar(sym, ts, (bid + ask) / 2)

        self.broker.sync(st)
        eq = equity(st, quotes)
        st["peak_equity"] = max(st["peak_equity"], eq)
        day = datetime.fromtimestamp(ts, timezone.utc).date().isoformat()
        if st["day"] != day:
            st["day"], st["day_start_equity"] = day, eq

        if st["halted"]:
            return eq

        # 1) 목표 달성 → 전량 청산 후 정지 (수익 확정)
        if eq >= cfg.target_equity:
            self._sell_all(ts, quotes, "목표 달성")
            st["halted"] = "GOAL"
            self._log(ts, f"🎯 목표 달성: ${eq:,.2f}. 운용 정지.")
            return eq

        # 2) 최대 낙폭 초과 → 전량 청산 후 정지 (사람이 재가동 판단)
        if eq <= st["peak_equity"] * (1 - cfg.max_drawdown_halt):
            self._sell_all(ts, quotes, "최대낙폭 차단")
            st["halted"] = "DRAWDOWN"
            self._log(ts, f"⛔ 고점 대비 -{cfg.max_drawdown_halt:.0%} 도달: ${eq:,.2f}. 운용 정지.")
            return eq

        daily_blocked = eq <= st["day_start_equity"] * (1 - cfg.daily_loss_halt)

        for sym, (bid, ask) in quotes.items():
            sig = strategy.evaluate(st["bars"].get(sym, []), cfg)
            if sig is None:
                continue
            pos = st["positions"].get(sym)
            if pos:
                pos["peak"] = max(pos["peak"], bid)
                pos["stop"] = max(pos["stop"], pos["peak"] - cfg.stop_atr_mult * sig.atr)
                if bid <= pos["stop"]:
                    self._exit(sym, ts, bid, "트레일링 스톱")
                elif sig.trend_down:
                    self._exit(sym, ts, bid, "추세 이탈")
            elif sig.trend_up and not daily_blocked:
                qty = strategy.position_size(eq, st["cash"], ask, sig.atr, cfg)
                filled = self.broker.buy(st, sym, qty, ask)
                if filled:
                    st["positions"][sym] = {
                        "qty": filled, "entry": ask, "peak": bid,
                        "stop": ask - cfg.stop_atr_mult * sig.atr,
                    }
                    self._log(ts, f"BUY  {sym} {filled:.8f} @ {ask:,.2f}")
        return equity(st, quotes)
