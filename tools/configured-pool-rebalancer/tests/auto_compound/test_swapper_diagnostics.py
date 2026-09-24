from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from configured_pool_rebalancer.swapper import V3Swapper
from configured_pool_rebalancer.auto_compound.swapper import CompoundSwapper
from configured_pool_rebalancer.models import DexType, PoolConfig


class SwapperDiagnosticsTests(unittest.TestCase):
    def test_kyber_non_json_response_returns_none_with_diagnostic(self):
        response = Mock()
        response.ok = True
        response.status_code = 200
        response.headers = {"Content-Type": "text/html"}
        response.text = "upstream unavailable"
        response.json.side_effect = ValueError("not json")
        swapper = V3Swapper("BNB", None)
        with patch("configured_pool_rebalancer.swapper.requests.get", return_value=response):
            with self.assertLogs("configured_pool_rebalancer", level="WARNING") as captured:
                result = swapper.get_kyber_route(
                    "0x0000000000000000000000000000000000000010",
                    "0x0000000000000000000000000000000000000020",
                    100,
                )
        self.assertIsNone(result)
        self.assertIn("JSON decode error", " ".join(captured.output))

    def test_provider_fallback_behavior_is_unchanged(self):
        swapper = V3Swapper("BNB", None)
        route = {
            "provider": "0x",
            "to": "0x0000000000000000000000000000000000000004",
            "data": "0x1234",
            "value": "0",
            "buyAmount": "99",
            "price_impact": 0,
        }
        swapper.get_kyber_route = Mock(return_value=None)
        swapper.get_0x_swap_quote = Mock(return_value=route)
        swapper.get_okx_swap_quote = Mock(return_value=None)
        routes = swapper.get_swap_routes(
            "0x0000000000000000000000000000000000000010",
            "0x0000000000000000000000000000000000000020",
            100,
            "0x0000000000000000000000000000000000000002",
        )
        self.assertEqual(routes, [route])

    def test_compound_refinement_is_capped_at_three_quotes(self):
        pool = PoolConfig(
            name="TEST",
            chain="BNB",
            pool_address="0x0000000000000000000000000000000000000001",
            dex_type=DexType.PANCAKE_V3,
            managed_wallets=("0x0000000000000000000000000000000000000002",),
            bot_wallet="0x0000000000000000000000000000000000000002",
        )
        swapper = CompoundSwapper(pool)
        swapper.best_quote = Mock(return_value=None)
        result = swapper.best_refined_quote(
            "0x0000000000000000000000000000000000000010",
            "0x0000000000000000000000000000000000000020",
            10_000,
            pool.bot_wallet,
            lambda quote: quote.amount_out,
        )
        self.assertIsNone(result)
        self.assertEqual(swapper.best_quote.call_count, 3)


if __name__ == "__main__":
    unittest.main()
