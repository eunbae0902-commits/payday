"""체결 계층: 모의(PaperBroker) / 실거래(LiveBroker)."""
from __future__ import annotations

from .robinhood import RobinhoodCrypto, round_down


class PaperBroker:
    """호가(bid/ask)에 즉시 체결된다고 가정. 상태의 현금/수량을 직접 갱신."""

    def buy(self, state: dict, symbol: str, qty: float, ask: float) -> float:
        cost = qty * ask
        if qty <= 0 or cost > state["cash"]:
            return 0.0
        state["cash"] -= cost
        return qty

    def sell(self, state: dict, symbol: str, qty: float, bid: float) -> float:
        if qty <= 0:
            return 0.0
        state["cash"] += qty * bid
        return qty

    def sync(self, state: dict) -> None:
        pass


class LiveBroker:
    """Robinhood 시장가 주문. 주문 후 sync()로 실제 잔고와 맞춘다."""

    def __init__(self, client: RobinhoodCrypto, symbols: list[str]):
        self.client = client
        self.pairs = {s: client.trading_pair(s) for s in symbols}

    def _qty(self, symbol: str, qty: float) -> str | None:
        pair = self.pairs[symbol]
        q = round_down(qty, pair["asset_increment"])
        return q if float(q) >= float(pair["min_order_size"]) else None

    def buy(self, state: dict, symbol: str, qty: float, ask: float) -> float:
        q = self._qty(symbol, qty)
        if not q:
            return 0.0
        self.client.market_order(symbol, "buy", q)
        return float(q)

    def sell(self, state: dict, symbol: str, qty: float, bid: float) -> float:
        q = self._qty(symbol, qty)
        if not q:
            return 0.0
        self.client.market_order(symbol, "sell", q)
        return float(q)

    def sync(self, state: dict) -> None:
        acct = self.client.account()
        state["cash"] = float(acct["buying_power"])
        held = self.client.holdings()
        for sym, pos in list(state["positions"].items()):
            qty = held.get(sym.split("-")[0], 0.0)
            if qty <= 0:
                del state["positions"][sym]
            else:
                pos["qty"] = qty
