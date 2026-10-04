"""업비트 공개 시세 API에서 과거 시간봉을 받아 백테스트용 CSV로 저장.

문서: https://docs.upbit.com/reference/분minute-캔들-1  (인증 불필요, 1회 최대 200개)
"""
from __future__ import annotations

import csv
import time
from datetime import datetime, timedelta, timezone

import requests

CANDLE_URL = "https://api.upbit.com/v1/candles/minutes/60"
PAGE = 200


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def fetch_hourly(market: str, start: datetime, end: datetime, session=None,
                 pause: float = 0.15) -> list[tuple[float, float]]:
    """[start, end) 구간의 시간봉 종가를 오래된 순으로 반환. 최신부터 거꾸로 페이지 조회."""
    http = session or requests
    out: dict[float, float] = {}
    cursor = end
    while cursor > start:
        resp = http.get(CANDLE_URL, params={
            "market": market, "count": PAGE,
            "to": cursor.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }, timeout=10)
        resp.raise_for_status()
        candles = resp.json()
        if not candles:
            break
        for c in candles:
            t = _utc(c["candle_date_time_utc"])
            if start <= t < end:
                out[t.timestamp()] = float(c["trade_price"])
        oldest = min(_utc(c["candle_date_time_utc"]) for c in candles)
        if oldest >= cursor:
            break
        cursor = oldest
        time.sleep(pause)              # 공개 API 초당 호출 제한 대응
    return sorted(out.items())


def write_csv(markets: list[str], start: datetime, end: datetime, path: str,
              warmup_hours: int = 300, session=None) -> int:
    """백테스트 지표 계산용으로 start 이전 warmup_hours만큼 더 받아 저장."""
    begin = start - timedelta(hours=warmup_hours)
    rows = 0
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "symbol", "close"])
        for m in markets:
            for ts, close in fetch_hourly(m, begin, end, session=session):
                w.writerow([int(ts), m, close])
                rows += 1
    return rows
