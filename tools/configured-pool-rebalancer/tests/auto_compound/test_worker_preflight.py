from __future__ import annotations

import unittest
from unittest.mock import patch

from configured_pool_rebalancer.auto_compound.eligibility import CompoundEligibilityEvaluator
from configured_pool_rebalancer.auto_compound.models import CompoundPosition
from configured_pool_rebalancer.auto_compound.planner import CompoundPlanner, sqrt_ratio_at_tick
from configured_pool_rebalancer.auto_compound.worker import ConfiguredPoolCompounder
from configured_pool_rebalancer.models import (
    AutoCompoundConfig,
    DexType,
    PoolConfig,
    PositionSnapshot,
    Slot0,
    WorkerConfig,
)


WALLET = "0x0000000000000000000000000000000000000002"
POOL_ADDRESS = "0x0000000000000000000000000000000000000001"
NPM_ADDRESS = "0x0000000000000000000000000000000000000003"
TOKEN0 = "0x0000000000000000000000000000000000000010"
TOKEN1 = "0x0000000000000000000000000000000000000020"


class _Eth:
    block_number = 100


class _W3:
    eth = _Eth()


class _Adapter:
    def __init__(self, amount0, amount1):
        self.amount0 = amount0
        self.amount1 = amount1

    def read_slot0(self):
        return Slot0(sqrt_ratio_at_tick(0), 0)

    def quote_collect(self, _position, _anchor_block=None):
        return self.amount0, self.amount1


class _Journal:
    def __init__(self):
        self.updates = []

    def update(self, job_id, **values):
        self.updates.append((job_id, values))


def make_pool(min_compound_usd=5.0) -> PoolConfig:
    return PoolConfig(
        name="TEST",
        chain="BAS",
        pool_address=POOL_ADDRESS,
        dex_type=DexType.PANCAKE_V3,
        managed_wallets=(WALLET,),
        bot_wallet=WALLET,
        npm_address=NPM_ADDRESS,
        token0_address=TOKEN0,
        token1_address=TOKEN1,
        token0_decimals=18,
        token1_decimals=18,
        min_swap_input_usd=0.25,
        auto_compound=AutoCompoundConfig(
            enabled=True,
            min_compound_usd=min_compound_usd,
            gas_cost_multiplier=3,
        ),
    )


def make_position() -> CompoundPosition:
    snapshot = PositionSnapshot(
        token_id=1,
        owner=WALLET,
        pool_address=POOL_ADDRESS,
        token0=TOKEN0,
        token1=TOKEN1,
        fee=500,
        tick_lower=-600,
        tick_upper=600,
        liquidity=10**18,
    )
    return CompoundPosition(snapshot, NPM_ADDRESS, DexType.PANCAKE_V3.value, "UNSTAKED")


def make_compounder(pool):
    compounder = ConfiguredPoolCompounder.__new__(ConfiguredPoolCompounder)
    compounder.config = WorkerConfig(pools=(pool,), dry_run=True)
    compounder.eligibility = CompoundEligibilityEvaluator()
    compounder.planner = CompoundPlanner()
    compounder._price_cache = {}
    compounder._token_price = lambda _chain, _token: 1.0
    compounder._native_price = lambda _chain: 1.0
    compounder._estimate_gas_usd = lambda *_args, **_kwargs: 0.1
    return compounder


class CompoundWorkerPreflightTests(unittest.TestCase):
    def test_below_minimum_fee_does_not_construct_swapper(self):
        pool = make_pool(min_compound_usd=5)
        compounder = make_compounder(pool)
        adapter = _Adapter(10**18, 0)
        with patch(
            "configured_pool_rebalancer.auto_compound.worker.CompoundSwapper"
        ) as swapper:
            result = compounder._evaluate_and_execute(_W3(), pool, adapter, make_position())
        self.assertEqual(result["reason"], "BELOW_MIN_COMPOUND")
        swapper.assert_not_called()

    def test_analytical_input_dust_uses_no_swap_path_without_provider(self):
        pool = make_pool(min_compound_usd=5)
        compounder = make_compounder(pool)
        adapter = _Adapter(10 * 10**18, 96 * 10**17)
        with patch(
            "configured_pool_rebalancer.auto_compound.worker.CompoundSwapper"
        ) as swapper:
            result = compounder._evaluate_and_execute(_W3(), pool, adapter, make_position())
        self.assertEqual(result["state"], "PREPARED")
        self.assertEqual(result["swap_decision"], "NO_SWAP_INPUT_DUST")
        swapper.assert_not_called()

    def test_no_swap_profitability_uses_reinvestable_value(self):
        pool = make_pool(min_compound_usd=19.4)
        compounder = make_compounder(pool)
        adapter = _Adapter(10 * 10**18, 96 * 10**17)
        with patch(
            "configured_pool_rebalancer.auto_compound.worker.CompoundSwapper"
        ) as swapper:
            result = compounder._evaluate_and_execute(_W3(), pool, adapter, make_position())
        self.assertEqual(result["reason"], "BELOW_MIN_COMPOUND")
        self.assertLess(result["reinvestable_value_usd"], result["fee_value_usd"])
        swapper.assert_not_called()

    def test_after_collect_input_dust_increases_without_provider(self):
        pool = make_pool(min_compound_usd=5)
        compounder = make_compounder(pool)
        compounder.journal = _Journal()
        compounder._increase = lambda *_args: {"state": "INCREASE_CALLED"}
        adapter = _Adapter(0, 0)
        with patch(
            "configured_pool_rebalancer.auto_compound.worker.CompoundSwapper"
        ) as swapper:
            result = compounder._continue_after_collect(
                _W3(),
                pool,
                adapter,
                make_position(),
                {"id": 9},
                10 * 10**18,
                96 * 10**17,
            )
        self.assertEqual(result["state"], "INCREASE_CALLED")
        swapper.assert_not_called()

    def test_after_collect_zero_no_swap_plan_waits_and_preserves_reservation(self):
        pool = make_pool(min_compound_usd=5)
        compounder = make_compounder(pool)
        compounder.journal = _Journal()
        adapter = _Adapter(0, 0)
        with patch(
            "configured_pool_rebalancer.auto_compound.worker.CompoundSwapper"
        ) as swapper:
            result = compounder._continue_after_collect(
                _W3(),
                pool,
                adapter,
                make_position(),
                {"id": 10},
                4 * 10**17,
                0,
            )
        self.assertEqual(result["state"], "WAITING_FOR_SWAP")
        self.assertEqual(result["reason"], "NO_REINVESTABLE_LIQUIDITY")
        self.assertFalse(any("reserved_amount0_raw" in values for _, values in compounder.journal.updates))
        swapper.assert_not_called()


if __name__ == "__main__":
    unittest.main()
