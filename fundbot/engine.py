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
        "vault": {},              # symbol -> 장기 보유 수량 (봇이 매도하지 않음)
        "vault_cost": 0.0,        # 장기 보유분 누적 매수금액
        "milestones_hit": [],
        "pending_lock": 0.0,      # 장기 보유분으로 옮길 대기 금액
        "stats": {"trades": 0, "wins": 0},
        "log": [],
    }


def load_state(cfg: Config) -> dict:
    p = Path(cfg.state_dir) / f"state_{cfg.mode}.json"
    if not p.exists():
        return new_state(cfg)
    state = json.loads(p.read_text())
    for k, v in new_state(cfg).items():   # 이전 버전 상태 파일 호환
        state.setdefault(k, v)
    return state


def save_state(cfg: Config, state: dict) -> None:
    d = Path(cfg.state_dir)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"state_{cfg.mode}.json").write_text(json.dumps(state, indent=2))


def equity(state: dict, quotes: dict[str, tuple[float, float]]) -> float:
    total = state["cash"]
    for sym, pos in state["positions"].items():
        total += pos["qty"] * quotes[sym][0]   # 매도호가(bid) 기준 평가
    return total


def vault_value(state: dict, quotes: dict[str, tuple[float, float]]) -> float:
    return sum(qty * quotes[sym][0] for sym, qty in state["vault"].items())


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
            st = self.state.setdefault("stats", {"trades": 0, "wins": 0})
            st["trades"] += 1
            st["wins"] += pnl > 0
            self._log(ts, f"SELL {sym} {filled:.8f} @ {bid:,.2f} ({reason}) PnL {pnl:+,.2f}")
            del self.state["positions"][sym]

    def _check_milestones(self, ts: float, total: float) -> None:
        """총자산이 새 단계를 넘으면 (단계 - 직전 단계) × lock_pct 만큼 장기 보유 대기."""
        cfg, st = self.cfg, self.state
        base = cfg.starting_capital
        for m in cfg.milestones:
            if m not in st["milestones_hit"] and total >= m:
                lock = (m - base) * cfg.milestone_lock_pct
                st["milestones_hit"].append(m)
                st["pending_lock"] += lock
                self._log(ts, f"🏁 단계 ${m:,.0f} 돌파 (총자산 ${total:,.2f}): ${lock:,.2f} 장기 보유로 이전 예정")
            base = m

    def _raise_cash(self, ts: float, quotes, amount: float) -> None:
        """부족한 현금만큼 보유 포지션을 같은 비율로 일부 매도."""
        st = self.state
        pos_value = sum(p["qty"] * quotes[s][0] for s, p in st["positions"].items())
        if pos_value <= 0:
            return
        frac = min(1.0, amount * 1.01 / pos_value)
        for sym in list(st["positions"]):
            pos, bid = st["positions"][sym], quotes[sym][0]
            if frac >= 0.999:
                self._exit(sym, ts, bid, "장기 보유 이전")
                continue
            filled = self.broker.sell(st, sym, pos["qty"] * frac, bid)
            if filled:
                pos["qty"] -= filled
                self._log(ts, f"SELL {sym} {filled:.8f} @ {bid:,.2f} (장기 보유 이전용 일부 매도)")

    def _settle_lock(self, ts: float, quotes) -> None:
        """대기 금액으로 장기 보유 종목을 매수. 현금이 부족하면 먼저 일부 매도.
        (실거래는 매도 대금이 다음 tick 잔고 동기화 후 반영되므로 매수가 한 tick 늦을 수 있다.)"""
        cfg, st = self.cfg, self.state
        need = st["pending_lock"]
        if need <= 0:
            return
        if st["cash"] < need:
            self._raise_cash(ts, quotes, need - st["cash"])
        if st["cash"] < need * 0.98:
            return
        sym = cfg.vault_symbol
        ask = quotes[sym][1]
        filled = self.broker.buy(st, sym, min(need, st["cash"] * 0.999) / ask, ask)
        st["pending_lock"] = 0.0
        if not filled:
            self._log(ts, f"장기 보유 이전 건너뜀: ${need:,.2f} (최소 주문 수량 미달)")
            return
        spent = filled * ask
        st["vault"][sym] = st["vault"].get(sym, 0.0) + filled
        st["vault_cost"] += spent
        # 이전한 금액은 손실이 아니므로 낙폭·일일손실 기준에서도 차감
        st["peak_equity"] -= spent
        st["day_start_equity"] -= spent
        self._log(ts, f"🔒 장기 보유 {sym} {filled:.8f} @ {ask:,.2f} (${spent:,.2f})")

    def tick(self, ts: float, quotes: dict[str, tuple[float, float]]) -> float:
        """한 번 운용하고 총자산(매매 자산 + 장기 보유분)을 반환."""
        cfg, st = self.cfg, self.state
        for sym in cfg.symbols:
            bid, ask = quotes[sym]
            self._record_bar(sym, ts, (bid + ask) / 2)

        self.broker.sync(st)
        eq = equity(st, quotes)
        total = eq + vault_value(st, quotes)
        st["peak_equity"] = max(st["peak_equity"], eq)
        day = datetime.fromtimestamp(ts, timezone.utc).date().isoformat()
        if st["day"] != day:
            st["day"], st["day_start_equity"] = day, eq

        if st["halted"]:
            return total

        # 1) 최종 목표 달성 → 매매 포지션 청산 후 정지 (장기 보유분은 유지)
        if total >= cfg.target_equity:
            self._sell_all(ts, quotes, "목표 달성")
            st["halted"] = "GOAL"
            self._log(ts, f"🎯 목표 달성: 총자산 ${total:,.2f}. 운용 정지.")
            return total

        # 2) 매매 자산 최대 낙폭 초과 → 매매 포지션 청산 후 정지 (사람이 재가동 판단)
        if eq <= st["peak_equity"] * (1 - cfg.max_drawdown_halt):
            self._sell_all(ts, quotes, "최대낙폭 차단")
            st["halted"] = "DRAWDOWN"
            self._log(ts, f"⛔ 고점 대비 -{cfg.max_drawdown_halt:.0%} 도달: ${eq:,.2f}. 운용 정지.")
            return total

        # 3) 단계 돌파 시 수익 일부를 장기 보유로 이전
        self._check_milestones(ts, total)
        self._settle_lock(ts, quotes)
        eq = equity(st, quotes)

        daily_blocked = eq <= st["day_start_equity"] * (1 - cfg.daily_loss_halt)
        entry_blocked = daily_blocked or st["pending_lock"] > 0

        for sym in cfg.symbols:
            bid, ask = quotes[sym]
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
            elif sig.trend_up and not entry_blocked:
                qty = strategy.position_size(eq, st["cash"], ask, sig.atr, cfg)
                filled = self.broker.buy(st, sym, qty, ask)
                if filled:
                    st["positions"][sym] = {
                        "qty": filled, "entry": ask, "peak": bid,
                        "stop": ask - cfg.stop_atr_mult * sig.atr,
                    }
                    self._log(ts, f"BUY  {sym} {filled:.8f} @ {ask:,.2f}")
        return equity(st, quotes) + vault_value(st, quotes)
