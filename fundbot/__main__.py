"""CLI: python -m fundbot {run,status,backtest,odds,reset}"""
from __future__ import annotations

import argparse
import sys
import time

from . import backtest, engine, odds
from .broker import LiveBroker, PaperBroker
from .config import load_config
from .robinhood import RobinhoodCrypto


def _quotes(client: RobinhoodCrypto, symbols: list[str]) -> dict[str, tuple[float, float]]:
    return {s: client.best_bid_ask(s) for s in symbols}


def cmd_run(args) -> None:
    cfg = load_config(args.config)
    if not (cfg.api_key and cfg.private_key_b64):
        sys.exit("시세 조회에도 RH_API_KEY / RH_PRIVATE_KEY가 필요합니다 (paper 모드 포함).")
    client = RobinhoodCrypto(cfg.api_key, cfg.private_key_b64)
    if cfg.mode == "live":
        if not args.i_understand_the_risk:
            sys.exit("live 모드는 --i-understand-the-risk 플래그가 있어야 실행됩니다.")
        broker = LiveBroker(client, cfg.symbols)
    else:
        broker = PaperBroker()
    state = engine.load_state(cfg)
    eng = engine.Engine(cfg, broker, state)
    while True:
        try:
            eq = eng.tick(time.time(), _quotes(client, cfg.symbols))
            engine.save_state(cfg, state)
            print(f"[{cfg.mode}] equity ${eq:,.2f} | 포지션 {list(state['positions'])} | halted={state['halted']}")
        except Exception as e:  # 네트워크 오류 등은 다음 tick에서 재시도
            print(f"tick 실패: {e}", file=sys.stderr)
        if args.once or state["halted"]:
            break
        time.sleep(args.interval)


def cmd_status(args) -> None:
    cfg = load_config(args.config)
    st = engine.load_state(cfg)
    print(f"모드={cfg.mode} 현금=${st['cash']:,.2f} 고점자산=${st['peak_equity']:,.2f} halted={st['halted']}")
    for sym, p in st["positions"].items():
        print(f"  {sym}: {p['qty']:.8f} @ {p['entry']:,.2f} stop {p['stop']:,.2f}")
    for line in st["log"][-15:]:
        print("  " + line)


def cmd_reset(args) -> None:
    cfg = load_config(args.config)
    st = engine.load_state(cfg)
    st["halted"] = None
    if not st["positions"]:          # 정지 시 전량 청산되므로 현금 = 현재 자산
        st["peak_equity"] = st["cash"]
    engine.save_state(cfg, st)
    print("정지 상태 해제. 고점 기준을 현재 자산으로 재설정했습니다.")


def cmd_backtest(args) -> None:
    cfg = load_config(args.config)
    res = backtest.run(cfg, backtest.load_csv(args.csv))
    for line in res.pop("log")[-20:]:
        print("  " + line)
    for k, v in res.items():
        print(f"{k}: {v:,.2f}" if isinstance(v, float) else f"{k}: {v}")


def cmd_odds(args) -> None:
    cfg = load_config(args.config)
    r = odds.goal_probability(cfg.starting_capital, cfg.target_equity, args.ret, args.vol,
                              args.years, cfg.max_drawdown_halt)
    print(f"가정: 연수익 {args.ret:.0%}, 연변동성 {args.vol:.0%}, 기간 {args.years}년, "
          f"낙폭정지 {cfg.max_drawdown_halt:.0%}")
    print(f"  목표 ${cfg.target_equity:,.0f} 도달 확률 : {r['p_goal']:.1%}")
    print(f"  낙폭정지로 중단될 확률   : {r['p_drawdown_halt']:.1%}")
    print(f"  기간 내 미도달(운용중)    : {r['p_still_running']:.1%}")
    if r["median_years_to_goal"]:
        print(f"  달성 시 중앙값 소요기간   : {r['median_years_to_goal']:.1f}년")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="fundbot")
    ap.add_argument("--config", default="config.toml")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="운용 시작 (paper/live는 config의 mode)")
    r.add_argument("--interval", type=int, default=300, help="tick 간격(초)")
    r.add_argument("--once", action="store_true", help="1회 tick 후 종료 (cron용)")
    r.add_argument("--i-understand-the-risk", action="store_true")
    r.set_defaults(func=cmd_run)

    sub.add_parser("status", help="현재 상태").set_defaults(func=cmd_status)
    sub.add_parser("reset", help="정지 해제").set_defaults(func=cmd_reset)

    b = sub.add_parser("backtest", help="CSV(timestamp,symbol,close) 백테스트")
    b.add_argument("csv")
    b.set_defaults(func=cmd_backtest)

    o = sub.add_parser("odds", help="목표 달성 확률 시뮬레이션")
    o.add_argument("--ret", type=float, default=0.30, help="기대 연수익률")
    o.add_argument("--vol", type=float, default=0.60, help="연변동성")
    o.add_argument("--years", type=float, default=5)
    o.set_defaults(func=cmd_odds)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
