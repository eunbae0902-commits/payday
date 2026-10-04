"""설정 로딩. config.toml(선택) + 환경변수(API 키)."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path


@dataclass
class Config:
    # 운용 모드: "paper"(모의) | "live"(실거래). 기본은 반드시 paper.
    mode: str = "paper"
    symbols: list[str] = field(default_factory=lambda: ["BTC-USD", "ETH-USD"])
    starting_capital: float = 500.0
    target_equity: float = 10_000.0

    # 전략 (시간봉 종가 기준)
    fast_ema: int = 24
    slow_ema: int = 72
    regime_ema: int = 200
    atr_period: int = 14
    stop_atr_mult: float = 3.0

    # 리스크 관리
    risk_per_trade: float = 0.02      # 1회 손절 시 잃는 금액 = 자산의 2%
    max_position_pct: float = 0.60    # 한 종목에 최대 자산의 60%
    max_drawdown_halt: float = 0.30   # 고점 대비 -30%면 전량 청산 후 정지
    daily_loss_halt: float = 0.06     # 하루 -6%면 당일 신규진입 중단
    spread_bps: float = 50.0          # 모의/백테스트용 체결 비용(스프레드) 가정

    state_dir: str = "state"

    # 환경변수에서만 읽는 비밀값 (파일에 저장 금지)
    api_key: str = ""
    private_key_b64: str = ""

    @property
    def warmup_bars(self) -> int:
        return max(self.slow_ema, self.regime_ema, self.atr_period) + 1


def load_config(path: str | os.PathLike | None = None) -> Config:
    cfg = Config()
    p = Path(path) if path else Path("config.toml")
    if p.exists():
        data = tomllib.loads(p.read_text(encoding="utf-8"))
        known = {f.name for f in fields(Config)} - {"api_key", "private_key_b64"}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"알 수 없는 설정 키: {sorted(unknown)}")
        for k, v in data.items():
            setattr(cfg, k, v)
    cfg.api_key = os.environ.get("RH_API_KEY", "")
    cfg.private_key_b64 = os.environ.get("RH_PRIVATE_KEY", "")
    validate(cfg)
    return cfg


def validate(cfg: Config) -> None:
    if cfg.mode not in ("paper", "live"):
        raise ValueError("mode는 'paper' 또는 'live'여야 합니다")
    if not 0 < cfg.risk_per_trade <= 0.05:
        raise ValueError("risk_per_trade는 0~5% 사이로 제한됩니다")
    if not 0 < cfg.max_position_pct <= 1:
        raise ValueError("max_position_pct는 0~100% 사이여야 합니다")
    if not 0 < cfg.max_drawdown_halt <= 0.5:
        raise ValueError("max_drawdown_halt는 0~50% 사이로 제한됩니다")
    if cfg.fast_ema >= cfg.slow_ema:
        raise ValueError("fast_ema < slow_ema 이어야 합니다")
    if cfg.mode == "live" and not (cfg.api_key and cfg.private_key_b64):
        raise ValueError("live 모드는 RH_API_KEY, RH_PRIVATE_KEY 환경변수가 필요합니다")
