"""목표 달성 확률 몬테카를로: '500→10,000'이 현실적으로 얼마나 가능한가."""
from __future__ import annotations

import math
import random


def goal_probability(start: float, target: float, annual_return: float, annual_vol: float,
                     years: float, max_drawdown_halt: float, paths: int = 10_000,
                     seed: int = 7, milestones: tuple[float, ...] = (),
                     lock_pct: float = 0.0, vault_return: float = 0.30,
                     vault_vol: float = 0.55, correlation: float = 0.6) -> dict:
    """매매 자산은 (annual_return, annual_vol)로, 장기 보유분은 (vault_return, vault_vol)로
    움직인다고 가정. 단계 돌파 시 (단계-직전단계)×lock_pct를 장기 보유로 옮긴다.
    매매가 낙폭 정지되어도 장기 보유분은 계속 보유하며, 기간 내 총자산이 목표에 닿으면 성공."""
    rng = random.Random(seed)
    dt = 1 / 52                                   # 주 단위 시뮬레이션
    steps = int(years * 52)
    drift = (annual_return - 0.5 * annual_vol ** 2) * dt
    shock = annual_vol * math.sqrt(dt)
    v_drift = (vault_return - 0.5 * vault_vol ** 2) * dt
    v_shock = vault_vol * math.sqrt(dt)
    rho_c = math.sqrt(1 - correlation ** 2)
    levels = [m for m in milestones if start < m < target]

    hit = halted = 0
    hit_weeks: list[int] = []
    finals: list[float] = []
    for _ in range(paths):
        eq = peak = start
        vault, next_i, base, stopped = 0.0, 0, start, False
        for w in range(1, steps + 1):
            z1 = rng.gauss(0, 1)
            if not stopped:
                eq *= math.exp(drift + shock * z1)
            if vault:
                vault *= math.exp(v_drift + v_shock * (correlation * z1 + rho_c * rng.gauss(0, 1)))
            if eq + vault >= target:
                hit += 1
                hit_weeks.append(w)
                break
            while not stopped and next_i < len(levels) and eq + vault >= levels[next_i]:
                lock = min(eq, (levels[next_i] - base) * lock_pct)
                eq, vault, peak = eq - lock, vault + lock, peak - lock
                base = levels[next_i]
                next_i += 1
            if not stopped:
                peak = max(peak, eq)
                if eq <= peak * (1 - max_drawdown_halt):
                    stopped = True
                    halted += 1
                    if not vault:
                        break
        finals.append(min(eq + vault, target))
    hit_weeks.sort()
    finals.sort()
    return {
        "p_goal": hit / paths,
        "p_drawdown_halt": halted / paths,     # 매매가 낙폭으로 멈춘 비율 (이후 목표 달성 포함)
        "median_years_to_goal": (hit_weeks[len(hit_weeks) // 2] / 52) if hit_weeks else None,
        "median_final": finals[len(finals) // 2],      # 기간 종료(또는 정지) 시 총자산 중앙값
        "p_above_start": sum(f > start for f in finals) / paths,
    }
