from __future__ import annotations

import unittest
from types import SimpleNamespace

from configured_pool_rebalancer.adapter import (
    AERODROME_GAUGE_DEPOSIT_TOPIC,
    AERODROME_GAUGE_WITHDRAW_TOPIC,
    AerodromeGaugeAdapter,
    PancakeV3MasterChefAdapter,
)
from configured_pool_rebalancer.models import DexType, PoolConfig, RebalancePlan, TxResult
from configured_pool_rebalancer.position_index import LogRangeTooLarge, PositionIndex


POOL = "0x0000000000000000000000000000000000000001"
WALLET = "0x0000000000000000000000000000000000000002"
TOKEN0 = "0x0000000000000000000000000000000000000003"
TOKEN1 = "0x0000000000000000000000000000000000000004"
GAUGE = "0x0000000000000000000000000000000000000005"
NPM = "0x0000000000000000000000000000000000000006"


class Call:
    def __init__(self, value=None):
        self.value = value

    def call(self):
        return self.value


class FakePoolFunctions:
    def slot0(self):
        return Call((123456, -42, 1, 2, 3, True))


class FakePoolContract:
    functions = FakePoolFunctions()


class FakeNpmFunctions:
    def __init__(self):
        self.mint_params = None
        self.approved = "0x0000000000000000000000000000000000000000"

    def positions(self, token_id):
        return Call((0, WALLET, TOKEN0, TOKEN1, 100, -200, 300, 999, 0, 0, 11, 22))

    def ownerOf(self, token_id):
        return Call(WALLET)

    def mint(self, params):
        self.mint_params = params
        return SimpleNamespace(label="mint", params=params)

    def getApproved(self, token_id):
        return Call(self.approved)

    def approve(self, to, token_id):
        return SimpleNamespace(label="approve", to=to, token_id=token_id)


class FakeNpmContract:
    def __init__(self):
        self.functions = FakeNpmFunctions()


class FakeGaugeFunctions:
    def __init__(self):
        self.deposited = None

    def deposit(self, token_id):
        self.deposited = token_id
        return SimpleNamespace(label="stake", token_id=token_id)


class FakeGaugeContract:
    def __init__(self):
        self.functions = FakeGaugeFunctions()


class FakeExecutor:
    dry_run = False

    def __init__(self):
        self.sent = []

    def send(self, call_fn, label, gas=None, value=0):
        self.sent.append((label, call_fn, gas, value))
        return TxResult(tx_hash=f"dry-run:{label}", dry_run=True, metadata={"label": label})


def make_pool() -> PoolConfig:
    return PoolConfig(
        name="AERO",
        chain="BAS",
        pool_address=POOL,
        dex_type=DexType.AERODROME_V3,
        managed_wallets=(WALLET,),
        bot_wallet=WALLET,
        token0_address=TOKEN0,
        token1_address=TOKEN1,
        token0_decimals=18,
        token1_decimals=18,
        fee=3000,
        tick_spacing=100,
        npm_address=NPM,
        staking_address=GAUGE,
    )


def make_adapter() -> AerodromeGaugeAdapter:
    adapter = AerodromeGaugeAdapter.__new__(AerodromeGaugeAdapter)
    adapter.pool = make_pool()
    adapter.pool_contract = FakePoolContract()
    adapter.npm_address = NPM
    adapter.npm = FakeNpmContract()
    adapter.gauge_address = GAUGE
    adapter.gauge = FakeGaugeContract()
    adapter.executor = FakeExecutor()
    adapter.w3 = SimpleNamespace(eth=SimpleNamespace(block_number=100))
    adapter.approve_if_needed = lambda *args, **kwargs: None
    adapter._mint_receipt_from_result = lambda result: None
    return adapter


class AerodromeAdapterTests(unittest.TestCase):
    def test_slot0_parses_six_output_aerodrome_shape(self):
        slot0 = make_adapter().read_slot0()

        self.assertEqual(slot0.sqrt_price_x96, 123456)
        self.assertEqual(slot0.tick, -42)

    def test_read_npm_position_maps_tick_spacing_to_position_fee(self):
        position = make_adapter().read_npm_position(7)

        self.assertEqual(position.fee, 100)
        self.assertEqual(position.tick_lower, -200)
        self.assertEqual(position.tick_upper, 300)
        self.assertEqual(position.tokens_owed0, 11)
        self.assertEqual(position.tokens_owed1, 22)

    def test_mint_uses_tick_spacing_and_zero_sqrt_price(self):
        adapter = make_adapter()
        plan = RebalancePlan(
            old_token_id=1,
            current_tick=0,
            old_tick_lower=-100,
            old_tick_upper=100,
            new_tick_lower=-50,
            new_tick_upper=150,
            amount0_desired=1000,
            amount1_desired=2000,
        )

        result, token_id = adapter.mint(plan)

        self.assertTrue(result.dry_run)
        self.assertIsNone(token_id)
        params = adapter.npm.functions.mint_params
        self.assertEqual(params[2], 100)
        self.assertEqual(params[-1], 0)

    def test_stake_approves_npm_then_deposits_to_gauge(self):
        adapter = make_adapter()

        result = adapter.stake(77)

        self.assertTrue(result.dry_run)
        self.assertEqual([item[0] for item in adapter.executor.sent], ["approve", "stake"])
        self.assertEqual(adapter.gauge.functions.deposited, 77)


class AerodromePositionIndexTests(unittest.TestCase):
    def test_aerodrome_gauge_event_sweep_adds_deposited_token_id(self):
        token_id = 77
        event = {
            "topics": [
                bytes.fromhex(AERODROME_GAUGE_DEPOSIT_TOPIC[2:]),
                int(WALLET, 16).to_bytes(32, "big"),
                int(token_id).to_bytes(32, "big"),
            ]
        }
        index = PositionIndex("runtime/cache", use_legacy_cache=False, use_db_cache=False)
        adapter = make_adapter()
        logs_w3 = SimpleNamespace(eth=SimpleNamespace(get_logs=lambda query: [event]))
        index._log_rpc_sources = lambda w3, pool: [("primary", logs_w3)]

        staked, unstaked = index._sweep_stake_logs(
            SimpleNamespace(),
            make_pool(),
            adapter,
            GAUGE,
            adapter.stake_event_topics(),
            1,
            1,
            5000,
        )

        self.assertEqual(staked, {token_id})
        self.assertEqual(unstaked, set())

    def test_log_sweep_splits_large_rpc_range(self):
        token_id = 77
        deposit_event = {
            "topics": [
                bytes.fromhex(AERODROME_GAUGE_DEPOSIT_TOPIC[2:]),
                int(WALLET, 16).to_bytes(32, "big"),
                int(token_id).to_bytes(32, "big"),
            ]
        }
        withdraw_event = {
            "topics": [
                bytes.fromhex(AERODROME_GAUGE_WITHDRAW_TOPIC[2:]),
                int(WALLET, 16).to_bytes(32, "big"),
                int(token_id).to_bytes(32, "big"),
            ]
        }
        index = PositionIndex("runtime/cache", use_legacy_cache=False, use_db_cache=False)
        adapter = make_adapter()
        index._log_rpc_sources = lambda w3, pool: [("primary", SimpleNamespace())]
        calls = []

        def fake_get_logs(rpc_sources, pool, start, end, query):
            calls.append((start, end))
            if (start, end) == (1, 4):
                raise LogRangeTooLarge("query returned more than 10000 results")
            if (start, end) == (1, 2):
                return [deposit_event]
            if (start, end) == (3, 4):
                return [withdraw_event]
            return []

        index._get_logs_with_rpc_fallback = fake_get_logs

        staked, unstaked = index._sweep_stake_logs(
            SimpleNamespace(),
            make_pool(),
            adapter,
            GAUGE,
            adapter.stake_event_topics(),
            1,
            4,
            4,
        )

        self.assertEqual(calls, [(1, 4), (1, 2), (3, 4)])
        self.assertEqual(staked, set())
        self.assertEqual(unstaked, {token_id})


class PancakeRegressionTests(unittest.TestCase):
    def test_pancake_stake_event_topics_remain_masterchef_topics(self):
        adapter = PancakeV3MasterChefAdapter.__new__(PancakeV3MasterChefAdapter)
        adapter.masterchef_address = GAUGE

        self.assertEqual(len(adapter.stake_event_topics()), 2)
        self.assertEqual(adapter.stake_event_contract_address(), GAUGE)


if __name__ == "__main__":
    unittest.main()
