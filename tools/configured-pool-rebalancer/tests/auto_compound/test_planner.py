from __future__ import annotations

import unittest

from configured_pool_rebalancer.auto_compound.models import CompoundPosition
from configured_pool_rebalancer.auto_compound.planner import (
    CompoundPlanner,
    amounts_for_liquidity_exact,
    liquidity_from_amounts_exact,
    sqrt_ratio_at_tick,
)
from configured_pool_rebalancer.models import PositionSnapshot, Slot0


TOKEN0 = "0x0000000000000000000000000000000000000010"
TOKEN1 = "0x0000000000000000000000000000000000000020"


def make_position() -> CompoundPosition:
    snapshot = PositionSnapshot(
        token_id=10,
        owner="0x0000000000000000000000000000000000000002",
        pool_address="0x0000000000000000000000000000000000000001",
        token0=TOKEN0,
        token1=TOKEN1,
        fee=500,
        tick_lower=-600,
        tick_upper=600,
        liquidity=10**18,
    )
    return CompoundPosition(snapshot, "0x0000000000000000000000000000000000000003", "pancake_v3", "UNSTAKED")


class CompoundPlannerTests(unittest.TestCase):
    def setUp(self):
        self.position = make_position()
        self.slot = Slot0(sqrt_ratio_at_tick(0), 0)
        self.planner = CompoundPlanner()

    def test_balanced_fees_skip_swap(self):
        amount0, amount1 = amounts_for_liquidity_exact(10**20, self.slot.sqrt_price_x96, -600, 600)
        plan = self.planner.build_swap_plan(self.position, self.slot, amount0, amount1)
        self.assertTrue(plan.skip_swap)

    def test_token0_excess_swaps_token0_to_token1_without_exceeding_reservation(self):
        plan = self.planner.build_swap_plan(self.position, self.slot, 10**18, 0)
        self.assertEqual(plan.token_in.lower(), TOKEN0.lower())
        self.assertEqual(plan.token_out.lower(), TOKEN1.lower())
        self.assertGreater(plan.amount_in, 0)
        self.assertLessEqual(plan.amount_in, 10**18)

    def test_token1_excess_swaps_token1_to_token0(self):
        plan = self.planner.build_swap_plan(self.position, self.slot, 0, 10**18)
        self.assertEqual(plan.token_in.lower(), TOKEN1.lower())
        self.assertEqual(plan.token_out.lower(), TOKEN0.lower())

    def test_liquidity_plan_never_exceeds_reserved_amounts(self):
        plan = self.planner.build_liquidity_plan(self.position, self.slot, 10**18, 10**18, 50)
        self.assertLessEqual(plan.amount0_desired, 10**18)
        self.assertLessEqual(plan.amount1_desired, 10**18)
        self.assertGreater(plan.expected_liquidity, 0)

    def test_integer_liquidity_roundtrip_is_conservative(self):
        liquidity = liquidity_from_amounts_exact(self.slot.sqrt_price_x96, -600, 600, 10**18, 10**18)
        amount0, amount1 = amounts_for_liquidity_exact(liquidity, self.slot.sqrt_price_x96, -600, 600)
        self.assertLessEqual(amount0, 10**18)
        self.assertLessEqual(amount1, 10**18)

    def test_tick_math_matches_canonical_boundaries(self):
        self.assertEqual(sqrt_ratio_at_tick(-887272), 4295128739)
        self.assertEqual(
            sqrt_ratio_at_tick(887272),
            1461446703485210103287273052203988822378723970342,
        )


if __name__ == "__main__":
    unittest.main()
