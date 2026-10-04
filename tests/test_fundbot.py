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
    assert math.isclose(r["p_goal"] + r["p_drawdown_halt"] + r["p_still_running"], 1)
