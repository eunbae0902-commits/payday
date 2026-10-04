"""CLI: python -m fundbot {run,status,backtest,compare,fetch-upbit,odds,reset}"""
from __future__ import annotations

import argparse
import dataclasses
import sys
import time

from datetime import datetime, timezone

from . import backtest, engine, odds, upbit_data
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
        broker = LiveBroker(client, cfg.quote_symbols)
    else:
        broker = PaperBroker()
    state = engine.load_state(cfg)
    eng = engine.Engine(cfg, broker, state)
    while True:
        try:
            eq = eng.tick(time.time(), _quotes(client, cfg.quote_symbols))
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
    for sym, qty in st["vault"].items():
        print(f"  🔒 장기 보유 {sym}: {qty:.8f} (누적 매수 ${st['vault_cost']:,.2f})")
    print(f"  돌파한 단계: {st['milestones_hit'] or '없음'}")
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
    res = backtest.run(cfg, backtest.load_csv(args.csv), _day(args.start), _day(args.end))
    for line in res.pop("log")[-20:]:
        print("  " + line)
    for k, v in res.items():
        if isinstance(v, dict):
            v = ", ".join(f"{s} {p:+.1f}%" for s, p in v.items())
        print(f"{k}: {v:,.2f}" if isinstance(v, float) else f"{k}: {v}")


def cmd_compare(args) -> None:
    """같은 데이터·같은 설정에서 판단 단위(봉 길이)만 바꿔 비교."""
    base = load_config(args.config)
    rows = backtest.load_csv(args.csv)
    start, end = _day(args.start), _day(args.end)
    print(f"{'판단 단위':<10}{'수익률':>9}{'최대낙폭':>9}{'매매횟수':>8}{'승률':>8}  정지/단계")
    hold = None
    for h in args.bar_hours:
        cfg = dataclasses.replace(base, bar_hours=h)
        res = backtest.run(cfg, rows, start, end)
        hold = res["buy_and_hold_pct"]
        label = {1: "1시간봉", 4: "4시간봉", 24: "일봉"}.get(h, f"{h}시간봉")
        extra = res["halted"] or "-"
        if res["milestones_hit"]:
            extra += f" / 단계 {len(res['milestones_hit'])}개 돌파"
        print(f"{label:<10}{res['return_pct']:>+8.1f}%{-res['max_drawdown_pct']:>+8.1f}%"
              f"{res['trades']:>8}{res['win_rate_pct']:>7.0f}%  {extra}")
    if hold:
        print("단순 보유: " + ", ".join(f"{s} {p:+.1f}%" for s, p in hold.items()))


def _day(s: str | None) -> float | None:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc).timestamp() if s else None


def cmd_fetch_upbit(args) -> None:
    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
    n = upbit_data.write_csv(args.markets, start, end, args.out, warmup_hours=args.warmup_days * 24)
    print(f"{args.out}: {n:,}개 시간봉 저장 ({', '.join(args.markets)}, 지표 준비용 {args.warmup_days}일 포함)")


def cmd_odds(args) -> None:
    cfg = load_config(args.config)
    print(f"가정: 매매 연수익 {args.ret:.0%}/변동성 {args.vol:.0%}, 기간 {args.years}년, "
          f"낙폭정지 {cfg.max_drawdown_halt:.0%}, BTC 장기보유 연수익 {args.btc_ret:.0%}/변동성 {args.btc_vol:.0%}")
    for label, ms, pct in (("단계 이전 없음", (), 0.0),
                           (f"단계 {[int(m) for m in cfg.milestones]} × {cfg.milestone_lock_pct:.0%} 이전",
                            tuple(cfg.milestones), cfg.milestone_lock_pct)):
        r = odds.goal_probability(cfg.starting_capital, cfg.target_equity, args.ret, args.vol,
                                  args.years, cfg.max_drawdown_halt, milestones=ms, lock_pct=pct,
                                  vault_return=args.btc_ret, vault_vol=args.btc_vol)
        yrs = f"{r['median_years_to_goal']:.1f}년" if r["median_years_to_goal"] else "-"
        print(f"  [{label}] 목표 도달 {r['p_goal']:.1%} | 원금 이상 {r['p_above_start']:.1%} | "
              f"최종 총자산 중앙값 ${r['median_final']:,.0f} | 달성 중앙값 {yrs}")


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
    b.add_argument("--start", help="매매 시작일 YYYY-MM-DD (이전 데이터는 지표 준비용)")
    b.add_argument("--end", help="매매 종료일 YYYY-MM-DD (해당일 미포함)")
    b.set_defaults(func=cmd_backtest)

    c = sub.add_parser("compare", help="판단 단위(1h/4h/1d)별 백테스트 비교")
    c.add_argument("csv")
    c.add_argument("--start")
    c.add_argument("--end")
    c.add_argument("--bar-hours", type=int, nargs="+", default=[1, 4, 24])
    c.set_defaults(func=cmd_compare)

    fu = sub.add_parser("fetch-upbit", help="업비트 과거 시간봉을 CSV로 저장")
    fu.add_argument("--markets", nargs="+", default=["KRW-BTC", "KRW-ETH"])
    fu.add_argument("--start", required=True, help="YYYY-MM-DD (UTC)")
    fu.add_argument("--end", required=True, help="YYYY-MM-DD (UTC, 미포함)")
    fu.add_argument("--out", required=True)
    fu.add_argument("--warmup-days", type=int, default=220,
                    help="시작일 이전 지표 준비 기간 (일봉 EMA200에 필요한 약 210일 이상 권장)")
    fu.set_defaults(func=cmd_fetch_upbit)

    o = sub.add_parser("odds", help="목표 달성 확률 시뮬레이션")
    o.add_argument("--ret", type=float, default=0.30, help="기대 연수익률")
    o.add_argument("--vol", type=float, default=0.60, help="연변동성")
    o.add_argument("--years", type=float, default=5)
    o.add_argument("--btc-ret", type=float, default=0.30, help="장기 보유 BTC 기대 연수익률")
    o.add_argument("--btc-vol", type=float, default=0.55, help="장기 보유 BTC 연변동성")
    o.set_defaults(func=cmd_odds)

    args = ap.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
