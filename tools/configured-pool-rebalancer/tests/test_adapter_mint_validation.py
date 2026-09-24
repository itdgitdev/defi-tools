from __future__ import annotations

import unittest
from types import SimpleNamespace

from configured_pool_rebalancer.abi import INCREASE_LIQUIDITY_TOPIC
from configured_pool_rebalancer.adapter import PancakeV3MasterChefAdapter
from configured_pool_rebalancer.models import DexType, PoolConfig, PositionSnapshot, RebalancePlan


TOKEN0 = "0x0000000000000000000000000000000000000003"
TOKEN1 = "0x0000000000000000000000000000000000000004"
WALLET = "0x0000000000000000000000000000000000000002"
MASTERCHEF = "0x0000000000000000000000000000000000000005"
NPM = "0x0000000000000000000000000000000000000006"


def make_pool() -> PoolConfig:
    return PoolConfig(
        name="TEST",
        chain="BASE",
        pool_address="0x0000000000000000000000000000000000000001",
        dex_type=DexType.PANCAKE_V3_MASTERCHEF,
        managed_wallets=(WALLET,),
        bot_wallet=WALLET,
        token0_address=TOKEN0,
        token1_address=TOKEN1,
        fee=2500,
    )


def make_plan() -> RebalancePlan:
    return RebalancePlan(
        old_token_id=1,
        current_tick=0,
        old_tick_lower=-100,
        old_tick_upper=100,
        new_tick_lower=-50,
        new_tick_upper=50,
        amount0_desired=100,
        amount1_desired=200,
    )


def make_position(**overrides) -> PositionSnapshot:
    data = {
        "token_id": 2,
        "owner": WALLET,
        "pool_address": "0x0000000000000000000000000000000000000001",
        "token0": TOKEN0,
        "token1": TOKEN1,
        "fee": 2500,
        "tick_lower": -50,
        "tick_upper": 50,
        "liquidity": 1,
    }
    data.update(overrides)
    return PositionSnapshot(**data)


def make_adapter(position_or_positions) -> PancakeV3MasterChefAdapter:
    adapter = PancakeV3MasterChefAdapter.__new__(PancakeV3MasterChefAdapter)
    adapter.pool = make_pool()
    adapter.masterchef_address = MASTERCHEF
    adapter.npm_address = NPM
    adapter.w3 = SimpleNamespace(eth=SimpleNamespace(block_number=100))
    if isinstance(position_or_positions, list):
        positions = list(position_or_positions)

        def read_npm_position(token_id, owner=None):
            item = positions.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        adapter.read_npm_position = read_npm_position
    else:
        adapter.read_npm_position = lambda token_id, owner=None: position_or_positions
    return adapter


class AdapterMintValidationTests(unittest.TestCase):
    def test_owner_mismatch_reason_is_specific(self):
        adapter = make_adapter(make_position(owner="0x0000000000000000000000000000000000000009"))
        ok, reason = adapter._validate_minted_position(2, make_plan(), attempts=1, sleep_seconds=0)
        self.assertFalse(ok)
        self.assertIn("owner mismatch", reason)

    def test_pool_mismatch_reason_is_specific(self):
        adapter = make_adapter(make_position(fee=10000))
        ok, reason = adapter._validate_minted_position(2, make_plan(), attempts=1, sleep_seconds=0)
        self.assertFalse(ok)
        self.assertIn("pool mismatch", reason)

    def test_range_mismatch_reason_is_specific(self):
        adapter = make_adapter(make_position(tick_lower=-60))
        ok, reason = adapter._validate_minted_position(2, make_plan(), attempts=1, sleep_seconds=0)
        self.assertTrue(ok)
        self.assertIn("range mismatch", reason)

    def test_range_mismatch_detail_allows_stake_with_warning(self):
        adapter = make_adapter(make_position(tick_lower=-60))
        result = adapter._validate_minted_position_detail(2, make_plan(), attempts=1, sleep_seconds=0)
        self.assertTrue(result.can_stake)
        self.assertEqual(result.status, "VALID_WITH_RANGE_WARNING")
        self.assertIn("range mismatch", result.reason)

    def test_zero_liquidity_reason_is_specific(self):
        adapter = make_adapter(make_position(liquidity=0))
        ok, reason = adapter._validate_minted_position(2, make_plan(), attempts=1, sleep_seconds=0)
        self.assertFalse(ok)
        self.assertEqual(reason, "zero liquidity")

    def test_transient_position_read_failure_retries(self):
        adapter = make_adapter([RuntimeError("rpc stale"), make_position()])
        ok, reason = adapter._validate_minted_position(2, make_plan(), attempts=2, sleep_seconds=0)
        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_fallback_rpc_can_validate_after_primary_read_failure(self):
        adapter = make_adapter(make_position())
        primary = adapter.w3
        fallback = SimpleNamespace()
        adapter._mint_validation_rpc_sources = lambda: [("primary", primary), ("fallback-1:test", fallback)]

        def read_with_w3(w3, token_id, owner=None):
            if w3 is primary:
                raise RuntimeError("primary stale")
            return make_position()

        adapter._read_npm_position_with_w3 = read_with_w3
        result = adapter._validate_minted_position_detail(2, make_plan(), attempts=1, sleep_seconds=0)
        self.assertEqual(result.status, "VALID")
        self.assertEqual(result.rpc_label, "fallback-1:test")

    def test_mint_receipt_parse_ignores_non_npm_increase_liquidity_logs(self):
        adapter = make_adapter(make_position())
        receipt = {
            "transactionHash": "0x" + "a" * 64,
            "logs": [
                {
                    "address": "0x0000000000000000000000000000000000000009",
                    "topics": [INCREASE_LIQUIDITY_TOPIC, "0x" + "0" * 63 + "1"],
                },
                {
                    "address": NPM,
                    "topics": [INCREASE_LIQUIDITY_TOPIC, "0x" + "0" * 63 + "2"],
                },
            ],
        }
        self.assertEqual(adapter._new_token_id_from_mint_receipt(receipt), 2)

    def test_mint_receipt_parse_returns_none_without_npm_log(self):
        adapter = make_adapter(make_position())
        receipt = {
            "transactionHash": "0x" + "a" * 64,
            "logs": [
                {
                    "address": "0x0000000000000000000000000000000000000009",
                    "topics": [INCREASE_LIQUIDITY_TOPIC, "0x" + "0" * 63 + "1"],
                },
            ],
        }
        self.assertIsNone(adapter._new_token_id_from_mint_receipt(receipt))


if __name__ == "__main__":
    unittest.main()
