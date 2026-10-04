"""추세추종 전략: EMA 크로스 + 장기 추세 필터 + ATR 트레일링 스톱."""
from __future__ import annotations

from dataclasses import dataclass

from .config import Config
from .indicators import atr_close, ema


@dataclass
class Signal:
    trend_up: bool      # 진입 조건 충족
    trend_down: bool    # 청산 조건 충족
    atr: float


def evaluate(closes: list[float], cfg: Config) -> Signal | None:
    if len(closes) < cfg.warmup_bars:
        return None
    fast, slow = ema(closes, cfg.fast_ema), ema(closes, cfg.slow_ema)
    regime = ema(closes, cfg.regime_ema)
    price = closes[-1]
    return Signal(
        trend_up=fast > slow and price > regime,
        trend_down=fast < slow,
        atr=atr_close(closes, cfg.atr_period),
    )


def position_size(equity: float, cash: float, price: float, atr: float, cfg: Config) -> float:
    """손절 시 손실이 equity*risk_per_trade가 되도록 수량 결정, 비중·현금 한도 적용."""
    stop_dist = cfg.stop_atr_mult * atr
    if stop_dist <= 0 or price <= 0:
        return 0.0
    qty_by_risk = equity * cfg.risk_per_trade / stop_dist
    qty_by_cap = equity * cfg.max_position_pct / price
    qty_by_cash = cash * 0.99 / price
    return max(0.0, min(qty_by_risk, qty_by_cap, qty_by_cash))
