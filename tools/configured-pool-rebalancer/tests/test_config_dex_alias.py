from __future__ import annotations

import unittest

from configured_pool_rebalancer.models import DexType, PositionStrategy
from configured_pool_rebalancer.settings import _pool_from_dict
from configured_pool_rebalancer.worker import ConfiguredPoolRebalancer
from configured_pool_rebalancer.adapter import AerodromeGaugeAdapter, PancakeV3MasterChefAdapter


WALLET = "0x0000000000000000000000000000000000000002"
POOL = "0x0000000000000000000000000000000000000001"


def raw_pool(dex_type: str) -> dict:
    return {
        "name": "TEST",
        "chain": "BAS",
        "pool_address": POOL,
        "dex_type": dex_type,
        "managed_wallets": [WALLET],
        "bot_wallet": WALLET,
    }


class ConfigDexAliasTests(unittest.TestCase):
    def test_dex_type_aliases_parse(self):
        self.assertEqual(_pool_from_dict(raw_pool("pancake_v3_masterchef")).dex_type, DexType.PANCAKE_V3_MASTERCHEF)
        self.assertEqual(_pool_from_dict(raw_pool("pancake_v3")).dex_type, DexType.PANCAKE_V3)
        self.assertEqual(_pool_from_dict(raw_pool("aerodrome_v3")).dex_type, DexType.AERODROME_V3)
        self.assertEqual(_pool_from_dict(raw_pool("aerodrome_gauge")).dex_type, DexType.AERODROME_GAUGE)

    def test_adapter_registry_resolves_aliases(self):
        self.assertIs(ConfiguredPoolRebalancer._build_adapter.__globals__["ADAPTER_REGISTRY"][DexType.PANCAKE_V3], PancakeV3MasterChefAdapter)
        self.assertIs(ConfiguredPoolRebalancer._build_adapter.__globals__["ADAPTER_REGISTRY"][DexType.AERODROME_V3], AerodromeGaugeAdapter)
        self.assertIs(ConfiguredPoolRebalancer._build_adapter.__globals__["ADAPTER_REGISTRY"][DexType.AERODROME_GAUGE], AerodromeGaugeAdapter)

    def test_private_key_prefix_env_parses_without_reusing_legacy_full_key_field(self):
        raw = raw_pool("pancake_v3")
        raw["private_key_env"] = "LEGACY_FULL_PRIVATE_KEY"
        raw["private_key_prefix_env"] = "TEST_PRIVATE_KEY_PREFIX"

        pool = _pool_from_dict(raw)

        self.assertEqual(pool.private_key_env, "LEGACY_FULL_PRIVATE_KEY")
        self.assertEqual(pool.private_key_prefix_env, "TEST_PRIVATE_KEY_PREFIX")

    def test_aerodrome_position_strategy_is_advisory_and_defaults_to_farm(self):
        default_pool = _pool_from_dict(raw_pool("aerodrome_v3"))
        self.assertEqual(default_pool.expected_position_strategy, PositionStrategy.FARM)

        raw = raw_pool("aerodrome_v3")
        raw["position_strategy"] = "fee"
        fee_pool = _pool_from_dict(raw)
        self.assertEqual(fee_pool.position_strategy, PositionStrategy.FEE)
        self.assertEqual(fee_pool.expected_position_strategy, PositionStrategy.FEE)

    def test_position_strategy_is_rejected_for_pancake(self):
        raw = raw_pool("pancake_v3")
        raw["position_strategy"] = "farm"
        with self.assertRaisesRegex(ValueError, "only supported for Aerodrome"):
            _pool_from_dict(raw)


if __name__ == "__main__":
    unittest.main()
