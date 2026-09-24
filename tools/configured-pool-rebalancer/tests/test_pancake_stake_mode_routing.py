from __future__ import annotations

import unittest

from configured_pool_rebalancer.adapter import PancakeV3MasterChefAdapter
from configured_pool_rebalancer.models import DexType, PoolConfig, PositionSnapshot, Slot0
from configured_pool_rebalancer.worker import ConfiguredPoolRebalancer


WALLET = "0x0000000000000000000000000000000000000002"


class _Functions:
    def __init__(self, label):
        self.label = label

    def multicall(self, calls):
        return self.label, calls


class _Contract:
    def __init__(self, label):
        self.label = label
        self.functions = _Functions(label)
        self.encoded = []

    def encode_abi(self, name, args):
        value = self.label, name, args
        self.encoded.append(value)
        return value


class _Executor:
    def __init__(self):
        self.calls = []

    def send(self, function, label, gas):
        self.calls.append((function, label, gas))
        return function


def make_pool() -> PoolConfig:
    return PoolConfig(
        name="TEST",
        chain="BNB",
        pool_address="0x0000000000000000000000000000000000000001",
        dex_type=DexType.PANCAKE_V3_MASTERCHEF,
        managed_wallets=(WALLET,),
        bot_wallet=WALLET,
        token0_address="0x0000000000000000000000000000000000000010",
        token1_address="0x0000000000000000000000000000000000000020",
        fee=100,
        pid=1,
    )


def make_position(staked: bool) -> PositionSnapshot:
    pool = make_pool()
    return PositionSnapshot(
        token_id=42,
        owner=WALLET,
        pool_address=pool.pool_address,
        token0=pool.token0_address,
        token1=pool.token1_address,
        fee=100,
        tick_lower=-100,
        tick_upper=100,
        liquidity=0,
        is_staked=staked,
    )


class PancakeStakeModeRoutingTests(unittest.TestCase):
    def make_adapter(self):
        adapter = PancakeV3MasterChefAdapter.__new__(PancakeV3MasterChefAdapter)
        adapter.pool = make_pool()
        adapter.masterchef = _Contract("masterchef")
        adapter.npm = _Contract("npm")
        adapter.executor = _Executor()
        return adapter

    def test_staked_position_withdraw_routes_through_masterchef(self):
        adapter = self.make_adapter()
        result = adapter.decrease_collect_withdraw(make_position(True), Slot0(2**96, 0))
        self.assertEqual(result[0], "masterchef")
        self.assertEqual([call[1] for call in adapter.masterchef.encoded], ["collect", "withdraw"])
        self.assertEqual(adapter.npm.encoded, [])

    def test_unstaked_position_withdraw_routes_through_npm(self):
        adapter = self.make_adapter()
        result = adapter.decrease_collect_withdraw(make_position(False), Slot0(2**96, 0))
        self.assertEqual(result[0], "npm")
        self.assertEqual([call[1] for call in adapter.npm.encoded], ["collect"])
        self.assertEqual(adapter.masterchef.encoded, [])

    def test_execution_revalidation_uses_actual_mode_not_expected_farm_strategy(self):
        class _ReadAdapter:
            def read_staked_positions(self, _token_ids):
                return {}

            def read_npm_position(self, token_id):
                position = make_position(False)
                position.liquidity = 1
                return position

        worker = ConfiguredPoolRebalancer.__new__(ConfiguredPoolRebalancer)
        current = worker._revalidate_position_for_execution(
            make_pool(),
            _ReadAdapter(),
            make_position(True),
        )
        self.assertFalse(current.is_staked)


if __name__ == "__main__":
    unittest.main()
