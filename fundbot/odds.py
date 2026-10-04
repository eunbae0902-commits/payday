"""목표 달성 확률 몬테카를로: '500→10,000'이 현실적으로 얼마나 가능한가."""
from __future__ import annotations

import math
import random


def goal_probability(start: float, target: float, annual_return: float, annual_vol: float,
                     years: float, max_drawdown_halt: float, paths: int = 10_000,
                     seed: int = 7) -> dict:
    rng = random.Random(seed)
    dt = 1 / 52                                   # 주 단위 시뮬레이션
    steps = int(years * 52)
    drift = (annual_return - 0.5 * annual_vol ** 2) * dt
    shock = annual_vol * math.sqrt(dt)
    hit = halted = 0
    hit_weeks: list[int] = []
    for _ in range(paths):
        eq = peak = start
        for w in range(1, steps + 1):
            eq *= math.exp(drift + shock * rng.gauss(0, 1))
            peak = max(peak, eq)
            if eq >= target:
                hit += 1
                hit_weeks.append(w)
                break
            if eq <= peak * (1 - max_drawdown_halt):
                halted += 1
                break
    hit_weeks.sort()
    return {
        "p_goal": hit / paths,
        "p_drawdown_halt": halted / paths,
        "p_still_running": 1 - (hit + halted) / paths,
        "median_years_to_goal": (hit_weeks[len(hit_weeks) // 2] / 52) if hit_weeks else None,
    }
