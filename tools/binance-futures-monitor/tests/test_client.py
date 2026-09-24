from __future__ import annotations

import unittest
from decimal import Decimal

import requests

from binance_futures_monitor.client import (
    BinanceFuturesClient,
    BinanceMonitorError,
)
from binance_futures_monitor.models import BinanceCredentials, MarketType


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class BinanceFuturesClientTests(unittest.TestCase):
    def test_rate_limit_does_not_require_json_payload(self):
        session = FakeSession([FakeResponse(ValueError("html response"), status_code=429)])
        client = BinanceFuturesClient(session)

        with self.assertRaisesRegex(BinanceMonitorError, "rate limit") as raised:
            client._signed_get(MarketType.USD_M, BinanceCredentials("key", "secret"))

        self.assertEqual(raised.exception.category, "RATE_LIMIT")

    def test_signed_request_retries_two_transient_failures_with_new_signatures(self):
        session = FakeSession([
            requests.ConnectionError("offline"),
            FakeResponse({}, status_code=503),
            FakeResponse([]),
        ])
        client = BinanceFuturesClient(session)

        result = client._signed_get(
            MarketType.USD_M, BinanceCredentials("key", "secret")
        )

        self.assertEqual(result, [])
        signed_urls = [url for url, _ in session.calls]
        self.assertEqual(len(set(signed_urls)), 3)

    def test_authentication_failure_is_not_retried(self):
        session = FakeSession([
            FakeResponse(
                {"code": -2015, "msg": "Invalid API-key, IP, or permissions."},
                status_code=400,
            ),
            FakeResponse([]),
        ])
        client = BinanceFuturesClient(session)

        with self.assertRaises(BinanceMonitorError) as raised:
            client._signed_get(
                MarketType.USD_M, BinanceCredentials("key", "wrong-secret")
            )

        self.assertEqual(raised.exception.category, "AUTH_OR_IP")
        self.assertEqual(len(session.calls), 1)

    def test_invalid_signature_has_dedicated_category_and_is_not_retried(self):
        session = FakeSession([
            FakeResponse(
                {"code": -1022, "msg": "Signature for this request is not valid."},
                status_code=400,
            ),
            FakeResponse([]),
        ])
        client = BinanceFuturesClient(session)

        with self.assertRaises(BinanceMonitorError) as raised:
            client.validate_credentials(
                MarketType.USD_M,
                BinanceCredentials("key", "wrong-secret"),
            )

        self.assertEqual(raised.exception.category, "INVALID_SIGNATURE")
        self.assertEqual(raised.exception.code, "-1022")
        self.assertEqual(len(session.calls), 1)

    def test_validate_credentials_uses_one_signed_position_request(self):
        session = FakeSession([FakeResponse([])])
        client = BinanceFuturesClient(session)

        client.validate_credentials(
            MarketType.COIN_M,
            BinanceCredentials("api-key", "secret-key"),
        )

        self.assertEqual(len(session.calls), 1)
        signed_url, request = session.calls[0]
        self.assertIn("/dapi/v1/positionRisk?", signed_url)
        self.assertIn("signature=", signed_url)
        self.assertNotIn("secret-key", signed_url)
        self.assertEqual(request["headers"]["X-MBX-APIKEY"], "api-key")

    def test_usd_m_multiplier_and_short_exposure(self):
        session = FakeSession([
            FakeResponse({"symbols": [{
                "symbol": "1000SHIBUSDT",
                "pair": "1000SHIBUSDT",
                "contractType": "PERPETUAL",
                "baseAsset": "1000SHIB",
                "quoteAsset": "USDT",
                "marginAsset": "USDT",
            }]}),
            FakeResponse([{
                "symbol": "1000SHIBUSDT",
                "positionSide": "SHORT",
                "positionAmt": "2",
                "entryPrice": "0.01",
                "breakEvenPrice": "0.0099",
                "markPrice": "0.008",
                "unRealizedProfit": "4",
                "notional": "-16",
                "updateTime": 123,
            }]),
        ])
        credentials = BinanceCredentials("api-key", "secret-key")

        positions = BinanceFuturesClient(session).fetch_positions(
            "main", MarketType.USD_M, credentials
        )

        self.assertEqual(len(positions), 1)
        position = positions[0]
        self.assertEqual(position.base_asset, "SHIB")
        self.assertEqual(position.contract_multiplier, Decimal("1000"))
        self.assertEqual(position.signed_base_exposure, Decimal("-2000"))
        self.assertEqual(position.position_amt_unit, "BASE_ASSET")
        signed_url, request = session.calls[1]
        self.assertIn("signature=", signed_url)
        self.assertNotIn("secret-key", signed_url)
        self.assertEqual(request["headers"]["X-MBX-APIKEY"], "api-key")

    def test_coin_m_contract_exposure_and_native_pnl(self):
        session = FakeSession([
            FakeResponse({"symbols": [{
                "symbol": "BTCUSD_PERP",
                "pair": "BTCUSD",
                "contractType": "PERPETUAL",
                "deliveryDate": 0,
                "baseAsset": "BTC",
                "quoteAsset": "USD",
                "marginAsset": "BTC",
                "contractSize": 100,
            }]}),
            FakeResponse([{
                "symbol": "BTCUSD_PERP",
                "positionSide": "LONG",
                "positionAmt": "3",
                "entryPrice": "50000",
                "markPrice": "60000",
                "unRealizedProfit": "0.001",
                "leverage": "10",
                "marginType": "cross",
                "updateTime": 456,
            }]),
        ])

        positions = BinanceFuturesClient(session).fetch_positions(
            "main", MarketType.COIN_M, BinanceCredentials("key", "secret")
        )

        position = positions[0]
        self.assertEqual(position.position_amt_unit, "CONTRACT")
        self.assertEqual(position.contract_size_quote, Decimal("100"))
        self.assertEqual(position.signed_base_exposure, Decimal("0.005"))
        self.assertEqual(position.notional_value, Decimal("300"))
        self.assertEqual(position.pnl_asset, "BTC")

    def test_zero_position_does_not_require_valid_mark_price(self):
        session = FakeSession([
            FakeResponse({"symbols": [{
                "symbol": "BTCUSD_PERP",
                "pair": "BTCUSD",
                "contractType": "PERPETUAL",
                "baseAsset": "BTC",
                "quoteAsset": "USD",
                "marginAsset": "BTC",
                "contractSize": 100,
            }]}),
            FakeResponse([{
                "symbol": "BTCUSD_PERP",
                "positionSide": "BOTH",
                "positionAmt": "0",
                "markPrice": "0",
            }]),
        ])

        positions = BinanceFuturesClient(session).fetch_positions(
            "main", MarketType.COIN_M, BinanceCredentials("key", "secret")
        )

        self.assertEqual(positions, [])

    def test_clock_error_refreshes_market_time_and_retries_once(self):
        session = FakeSession([
            FakeResponse({"symbols": [{
                "symbol": "ETHUSDT",
                "pair": "ETHUSDT",
                "contractType": "PERPETUAL",
                "baseAsset": "ETH",
                "quoteAsset": "USDT",
                "marginAsset": "USDT",
            }]}),
            FakeResponse({"code": -1021, "msg": "timestamp"}, status_code=400),
            FakeResponse({"serverTime": 1000000}),
            FakeResponse([]),
        ])

        positions = BinanceFuturesClient(session).fetch_positions(
            "main", MarketType.USD_M, BinanceCredentials("key", "secret")
        )

        self.assertEqual(positions, [])
        self.assertIn("/fapi/v1/time", session.calls[2][0])
        self.assertEqual(len(session.calls), 4)

    def test_coin_m_open_position_rejects_zero_mark_price(self):
        session = FakeSession([
            FakeResponse({"symbols": [{
                "symbol": "BTCUSD_PERP",
                "pair": "BTCUSD",
                "contractType": "PERPETUAL",
                "baseAsset": "BTC",
                "quoteAsset": "USD",
                "marginAsset": "BTC",
                "contractSize": 100,
            }]}),
            FakeResponse([{
                "symbol": "BTCUSD_PERP",
                "positionSide": "BOTH",
                "positionAmt": "1",
                "markPrice": "0",
            }]),
        ])

        with self.assertRaisesRegex(BinanceMonitorError, "invalid markPrice"):
            BinanceFuturesClient(session).fetch_positions(
                "main", MarketType.COIN_M, BinanceCredentials("key", "secret")
            )


if __name__ == "__main__":
    unittest.main()
