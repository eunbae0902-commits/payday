import base64
import math

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, PrivateFormat, NoEncryption

from fundbot import backtest, odds, strategy
from fundbot.broker import PaperBroker
from fundbot.config import Config
from fundbot.engine import Engine, new_state
from fundbot.indicators import atr_close, ema
from fundbot.robinhood import round_down, sign_message


def small_cfg(**kw):
    c = Config(symbols=["BTC-USD"], fast_ema=3, slow_ema=6, regime_ema=10, atr_period=3)
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def quotes(p):
    return {"BTC-USD": (p, p)}


def test_ema_constant_series():
    assert ema([5.0] * 20, 10) == 5.0


def test_atr_close():
    assert atr_close([1, 2, 4, 7], 3) == 2.0


def test_signature_verifies():
    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    sig = sign_message(base64.b64encode(seed).decode(), "hello")
    key.public_key().verify(base64.b64decode(sig), b"hello")  # 예외 없으면 통과


def test_round_down_to_increment():
    assert round_down(0.123456789, "0.00001") == "0.12345"


def test_position_size_respects_risk_and_caps():
    cfg = Config()
    # 손절폭이 넓으면 리스크 한도(자산의 2%)가 수량을 결정
    qty = strategy.position_size(1000, 1000, 100, atr=5, cfg=cfg)
    assert math.isclose(qty * cfg.stop_atr_mult * 5, 1000 * cfg.risk_per_trade)
    # 손절폭이 좁으면 비중 한도(60%)가 수량을 결정
    qty = strategy.position_size(1000, 1000, 100, atr=0.01, cfg=cfg)
    assert math.isclose(qty * 100, 1000 * cfg.max_position_pct)


def test_uptrend_buys_then_downtrend_sells():
    cfg = small_cfg()
    st = new_state(cfg)
    eng = Engine(cfg, PaperBroker(), st)
    t = 0
    for p in [100 + i for i in range(20)]:
        eng.tick(t, quotes(p)); t += 3600
    assert "BTC-USD" in st["positions"]
    for p in [119 - 3 * i for i in range(15)]:
        eng.tick(t, quotes(p)); t += 3600
    assert "BTC-USD" not in st["positions"]
    assert any("SELL" in l for l in st["log"])


def test_goal_liquidates_and_halts():
    cfg = small_cfg(target_equity=520, max_position_pct=1.0, risk_per_trade=0.05)
    st = new_state(cfg)
    eng = Engine(cfg, PaperBroker(), st)
    t = 0
    for p in [100 * 1.03 ** i for i in range(40)]:
        eng.tick(t, quotes(p)); t += 3600
    assert st["halted"] == "GOAL" and not st["positions"] and st["cash"] >= 520


def test_drawdown_halt():
    cfg = small_cfg(max_drawdown_halt=0.01, stop_atr_mult=50, max_position_pct=1.0, risk_per_trade=0.05)
    st = new_state(cfg)
    eng = Engine(cfg, PaperBroker(), st)
    t = 0
    for p in [100 + i for i in range(15)] + [60]:
        eng.tick(t, quotes(p)); t += 3600
    assert st["halted"] == "DRAWDOWN" and not st["positions"]


def test_same_hour_updates_bar_instead_of_appending():
    cfg = small_cfg()
    st = new_state(cfg)
    eng = Engine(cfg, PaperBroker(), st)
    eng.tick(0, quotes(100)); eng.tick(600, quotes(101)); eng.tick(3600, quotes(102))
    assert st["bars"]["BTC-USD"] == [101, 102]


def test_backtest_csv(tmp_path):
    f = tmp_path / "px.csv"
    lines = ["timestamp,symbol,close"] + [f"{i*3600},BTC-USD,{100 + i}" for i in range(50)]
    f.write_text("\n".join(lines))
    res = backtest.run(small_cfg(), backtest.load_csv(str(f)))
    assert res["final_equity"] > 500


def test_odds_bounds():
    r = odds.goal_probability(500, 10000, 0.3, 0.6, 5, 0.3, paths=2000)
    assert 0 <= r["p_goal"] <= 1
    assert r["p_goal"] + r["p_drawdown_halt"] <= 1 + 1e-9


def test_odds_without_milestones_unchanged_by_vault_params():
    a = odds.goal_probability(500, 10000, 0.3, 0.6, 5, 0.3, paths=1000)
    b = odds.goal_probability(500, 10000, 0.3, 0.6, 5, 0.3, paths=1000, vault_return=0.9)
    assert a == b


# --- 단계별 수익 확정 ---

def test_milestone_locks_profit_into_vault_without_drawdown_halt():
    cfg = small_cfg(milestones=[600.0, 700.0], milestone_lock_pct=0.5,
                    max_position_pct=1.0, risk_per_trade=0.05, max_drawdown_halt=0.10)
    st = new_state(cfg)
    eng = Engine(cfg, PaperBroker(), st)
    t, total = 0, 0
    for p in [100 * 1.02 ** i for i in range(80)]:
        total = eng.tick(t, quotes(p)); t += 3600
        if st["milestones_hit"] == [600.0, 700.0] and st["pending_lock"] == 0:
            break
    assert st["milestones_hit"] == [600.0, 700.0]
    # (600-500)*0.5 + (700-600)*0.5 = 100달러가 장기 보유로 이동
    assert abs(st["vault_cost"] - 100) < 1.5
    assert st["vault"]["BTC-USD"] > 0
    assert st["halted"] is None          # 이전 금액이 낙폭으로 오인되지 않음
    assert total >= 700


def test_vault_survives_drawdown_liquidation():
    cfg = small_cfg(milestones=[600.0], milestone_lock_pct=0.5, max_drawdown_halt=0.05,
                    max_position_pct=1.0, risk_per_trade=0.05)
    st = new_state(cfg)
    eng = Engine(cfg, PaperBroker(), st)
    t = 0
    for p in [100 * 1.03 ** i for i in range(40)]:
        eng.tick(t, quotes(p)); t += 3600
        if st["vault"]:
            break
    vault_qty = st["vault"]["BTC-USD"]
    eng.tick(t, quotes(p * 0.5))
    assert st["halted"] == "DRAWDOWN" and not st["positions"]
    assert st["vault"]["BTC-USD"] == vault_qty   # 장기 보유분은 팔지 않음


def test_multiple_milestones_in_one_jump():
    cfg = small_cfg(milestones=[600.0, 800.0], milestone_lock_pct=0.3)
    st = new_state(cfg)
    eng = Engine(cfg, PaperBroker(), st)
    eng._check_milestones(0, 900)
    assert st["milestones_hit"] == [600.0, 800.0]
    assert math.isclose(st["pending_lock"], (600 - 500) * 0.3 + (800 - 600) * 0.3)


def test_pending_lock_blocks_new_entries_until_settled():
    cfg = small_cfg()
    st = new_state(cfg)
    st["pending_lock"] = 1e9          # 현금으로 감당 불가능한 대기 금액
    eng = Engine(cfg, PaperBroker(), st)
    t = 0
    for p in [100 + i for i in range(20)]:
        eng.tick(t, quotes(p)); t += 3600
    assert not st["positions"]


def test_live_sync_excludes_vault_quantity():
    from fundbot.broker import LiveBroker

    class FakeClient:
        def trading_pair(self, s):
            return {"asset_increment": "0.00000001", "min_order_size": "0.000001"}
        def account(self):
            return {"buying_power": "123.45"}
        def holdings(self):
            return {"BTC": 0.015}

    broker = LiveBroker(FakeClient(), ["BTC-USD"])
    st = {"cash": 0, "positions": {"BTC-USD": {"qty": 0.01}}, "vault": {"BTC-USD": 0.005}}
    broker.sync(st)
    assert st["cash"] == 123.45
    assert math.isclose(st["positions"]["BTC-USD"]["qty"], 0.01)


def test_milestone_validation():
    import pytest
    from fundbot.config import validate
    with pytest.raises(ValueError):
        validate(Config(milestones=[2500.0, 1000.0]))
    with pytest.raises(ValueError):
        validate(Config(milestones=[10_000.0]))
    validate(Config())


# --- 업비트 시세 수집 / 기간 백테스트 ---

def test_fetch_hourly_paginates_backwards_and_clips_range():
    from datetime import datetime, timedelta, timezone
    from fundbot import upbit_data

    base = datetime(2022, 1, 1, tzinfo=timezone.utc)
    all_hours = [base + timedelta(hours=i) for i in range(500)]

    class Resp:
        def __init__(self, data): self.data = data
        def raise_for_status(self): pass
        def json(self): return self.data

    class Session:
        calls = 0
        def get(self, url, params, timeout):
            Session.calls += 1
            to = datetime.strptime(params["to"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            older = [h for h in all_hours if h < to][-params["count"]:]
            return Resp([{"candle_date_time_utc": h.strftime("%Y-%m-%dT%H:%M:%S"),
                          "trade_price": 100 + all_hours.index(h)} for h in reversed(older)])

    start, end = base + timedelta(hours=10), base + timedelta(hours=450)
    rows = upbit_data.fetch_hourly("KRW-BTC", start, end, session=Session(), pause=0)
    assert len(rows) == 440
    assert rows[0] == (start.timestamp(), 110.0)
    assert rows == sorted(rows)
    assert Session.calls >= 3


def test_backtest_period_uses_warmup_without_trading_and_reports_hold():
    cfg = small_cfg()
    rows = {i * 3600.0: {"BTC-USD": 100.0 + i} for i in range(60)}
    res = backtest.run(cfg, rows, start_ts=20 * 3600.0, end_ts=50 * 3600.0)
    assert math.isclose(res["buy_and_hold_pct"]["BTC-USD"], (149 / 120 - 1) * 100)
    # 워밍업 덕분에 시작 시점에 바로 진입 가능
    assert "BUY" in res["log"][0] and res["log"][0].startswith("1970-01-01 20:00")
