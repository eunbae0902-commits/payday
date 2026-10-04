"""종가 시계열 지표."""
from __future__ import annotations


def ema(values: list[float], period: int) -> float:
    if len(values) < period:
        raise ValueError("데이터 부족")
    k = 2 / (period + 1)
    e = sum(values[:period]) / period
    for v in values[period:]:
        e = v * k + e * (1 - k)
    return e


def atr_close(values: list[float], period: int) -> float:
    """종가 간 절대변동 평균 (고가/저가 없이 쓰는 ATR 근사)."""
    if len(values) < period + 1:
        raise ValueError("데이터 부족")
    window = values[-(period + 1):]
    return sum(abs(b - a) for a, b in zip(window, window[1:])) / period
