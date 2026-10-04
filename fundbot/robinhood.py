"""Robinhood Crypto Trading API 클라이언트 (공식 API, Ed25519 서명).

문서: https://docs.robinhood.com/crypto/trading/
"""
from __future__ import annotations

import base64
import json
import time
import uuid
from decimal import ROUND_DOWN, Decimal

import requests
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

BASE_URL = "https://trading.robinhood.com"


def sign_message(private_key_b64: str, message: str) -> str:
    seed = base64.b64decode(private_key_b64)
    key = Ed25519PrivateKey.from_private_bytes(seed[:32])
    return base64.b64encode(key.sign(message.encode("utf-8"))).decode("utf-8")


def round_down(value: float, increment: str) -> str:
    inc = Decimal(increment)
    return str((Decimal(str(value)) / inc).to_integral_value(rounding=ROUND_DOWN) * inc)


class RobinhoodCrypto:
    def __init__(self, api_key: str, private_key_b64: str, timeout: float = 10.0):
        self.api_key = api_key
        self.private_key_b64 = private_key_b64
        self.timeout = timeout
        self.session = requests.Session()

    def _headers(self, method: str, path: str, body: str) -> dict[str, str]:
        ts = str(int(time.time()))
        msg = f"{self.api_key}{ts}{path}{method}{body}"
        return {
            "x-api-key": self.api_key,
            "x-timestamp": ts,
            "x-signature": sign_message(self.private_key_b64, msg),
            "Content-Type": "application/json; charset=utf-8",
        }

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        body = json.dumps(payload) if payload is not None else ""
        resp = self.session.request(
            method, BASE_URL + path, headers=self._headers(method, path, body),
            data=body or None, timeout=self.timeout,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Robinhood API {method} {path} -> {resp.status_code}: {resp.text}")
        return resp.json() if resp.text else {}

    # --- 조회 ---
    def account(self) -> dict:
        return self._request("GET", "/api/v1/crypto/trading/accounts/")

    def trading_pair(self, symbol: str) -> dict:
        res = self._request("GET", f"/api/v1/crypto/trading/trading_pairs/?symbol={symbol}")
        return res["results"][0]

    def holdings(self) -> dict[str, float]:
        res = self._request("GET", "/api/v1/crypto/trading/holdings/")
        return {h["asset_code"]: float(h["total_quantity"]) for h in res.get("results", [])}

    def best_bid_ask(self, symbol: str) -> tuple[float, float]:
        res = self._request("GET", f"/api/v1/crypto/marketdata/best_bid_ask/?symbol={symbol}")
        q = res["results"][0]
        return float(q["bid_inclusive_of_sell_spread"]), float(q["ask_inclusive_of_buy_spread"])

    # --- 주문 ---
    def market_order(self, symbol: str, side: str, asset_quantity: str) -> dict:
        payload = {
            "client_order_id": str(uuid.uuid4()),
            "side": side,
            "symbol": symbol,
            "type": "market",
            "market_order_config": {"asset_quantity": asset_quantity},
        }
        return self._request("POST", "/api/v1/crypto/trading/orders/", payload)
