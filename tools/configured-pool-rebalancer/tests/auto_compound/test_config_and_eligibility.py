from __future__ import annotations

import unittest

from configured_pool_rebalancer.auto_compound.eligibility import CompoundEligibilityEvaluator
from configured_pool_rebalancer.auto_compound.abi import COMPOUND_MASTERCHEF_ABI, COMPOUND_NPM_ABI
from configured_pool_rebalancer.auto_compound.models import CompoundPosition
from configured_pool_rebalancer.models import (
    AutoCompoundConfig,
    DexType,
    PositionSnapshot,
    Slot0,
)
from configured_pool_rebalancer.settings import _expand_pool_dict, _pool_from_dict


WALLET = "0x0000000000000000000000000000000000000002"
POOL = "0x0000000000000000000000000000000000000001"
NPM = "0x0000000000000000000000000000000000000003"


def raw_pool() -> dict:
    return {
        "name": "TEST",
        "chain": "BAS",
        "pool_address": POOL,
        "dex_type": "pancake_v3",
        "managed_wallets": [WALLET],
        "bot_wallet": WALLET,
        "npm_address": NPM,
    }


def position(dex: DexType, *, staked: bool = False) -> CompoundPosition:
    snapshot = PositionSnapshot(
        token_id=1,
        owner=WALLET,
        pool_address=POOL,
        token0="0x0000000000000000000000000000000000000010",
        token1="0x0000000000000000000000000000000000000020",
        fee=500,
        tick_lower=-100,
        tick_upper=100,
        liquidity=10_000,
        is_staked=staked,
    )
    return CompoundPosition(snapshot, NPM, dex.value, "STAKED" if staked else "UNSTAKED")


class AutoCompoundConfigTests(unittest.TestCase):
    def test_feature_is_disabled_by_default(self):
        self.assertFalse(_pool_from_dict(raw_pool()).auto_compound.enabled)

    def test_pool_auto_compound_deep_merges_defaults(self):
        expanded = _expand_pool_dict(
            {"pool_defaults": {"auto_compound": {"enabled": True, "min_compound_usd": 8}}},
            {**raw_pool(), "auto_compound": {"gas_cost_multiplier": 4}},
        )
        parsed = _pool_from_dict(expanded)
        self.assertTrue(parsed.auto_compound.enabled)
        self.assertEqual(parsed.auto_compound.min_compound_usd, 8)
        self.assertEqual(parsed.auto_compound.gas_cost_multiplier, 4)

    def test_invalid_range_buffer_is_rejected(self):
        raw = raw_pool()
        raw["auto_compound"] = {"min_range_buffer_ratio": 0.5}
        with self.assertRaisesRegex(ValueError, "min_range_buffer_ratio"):
            _pool_from_dict(raw)

    def test_compound_abis_expose_no_reward_or_principal_removal_actions(self):
        names = {item.get("name") for item in [*COMPOUND_MASTERCHEF_ABI, *COMPOUND_NPM_ABI]}
        self.assertFalse(names & {"harvest", "getReward", "decreaseLiquidity", "mint", "burn", "withdraw"})


class EligibilityTests(unittest.TestCase):
    def setUp(self):
        self.evaluator = CompoundEligibilityEvaluator()
        self.config = AutoCompoundConfig(enabled=True, min_range_buffer_ratio=0.1)

    def test_pancake_staked_and_unstaked_are_allowed(self):
        for staked in (False, True):
            result = self.evaluator.evaluate_policy(
                position(DexType.PANCAKE_V3, staked=staked), Slot0(2**96, 0), self.config
            )
            self.assertTrue(result.eligible)

    def test_aerodrome_staked_is_rejected(self):
        result = self.evaluator.evaluate_policy(
            position(DexType.AERODROME_V3, staked=True), Slot0(2**96, 0), self.config
        )
        self.assertEqual(result.reason, "STAKE_POLICY")

    def test_upper_tick_is_exclusive(self):
        result = self.evaluator.evaluate_policy(
            position(DexType.PANCAKE_V3), Slot0(2**96, 100), self.config
        )
        self.assertEqual(result.reason, "OUT_OF_RANGE")

    def test_profitability_uses_user_threshold_and_gas_multiplier(self):
        config = AutoCompoundConfig(enabled=True, min_compound_usd=5, gas_cost_multiplier=3)
        self.assertFalse(self.evaluator.evaluate_profitability(config, 8, 3).eligible)
        self.assertTrue(self.evaluator.evaluate_profitability(config, 10, 3).eligible)


if __name__ == "__main__":
    unittest.main()
