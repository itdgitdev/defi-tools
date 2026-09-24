from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from configured_pool_rebalancer.auto_compound.models import CompoundDiscoveryResult
from configured_pool_rebalancer.auto_compound.position_index import (
    CompoundPositionIndex,
    _DbCandidates,
)
from configured_pool_rebalancer.models import (
    CompoundCandidate,
    DexType,
    PoolConfig,
    PositionSnapshot,
    StakeMode,
)


WALLET = "0x0000000000000000000000000000000000000002"
OTHER = "0x0000000000000000000000000000000000000009"
POOL_ADDRESS = "0x0000000000000000000000000000000000000001"
NPM_ADDRESS = "0x0000000000000000000000000000000000000003"
TOKEN0 = "0x0000000000000000000000000000000000000010"
TOKEN1 = "0x0000000000000000000000000000000000000020"


class _CallResult:
    def __init__(self, value):
        self.value = value

    def call(self):
        return self.value


class _Functions:
    def __init__(self, balance):
        self.balance = balance

    def balanceOf(self, _wallet):
        return _CallResult(self.balance)

    def ownerOf(self, _token_id):
        return _CallResult(WALLET)


class _Npm:
    def __init__(self, balance):
        self.functions = _Functions(balance)


class _Eth:
    def __init__(self, balance=1):
        self.block_number = 100
        self.npm = _Npm(balance)

    def contract(self, **_kwargs):
        return self.npm


class _W3:
    def __init__(self, balance=1):
        self.eth = _Eth(balance)


class _FakeMulticall:
    calls = []
    owner = WALLET

    class Call:
        def __init__(self, address, signature, args=None):
            self.address = address
            self.signature = signature
            self.args = args
            _FakeMulticall.calls.append(self)

    def __init__(self, _w3):
        self.pending = []

    def add(self, call):
        self.pending.append(call)

    def call(self):
        if self.pending and self.pending[0].signature.startswith("tokenOfOwnerByIndex"):
            return [42 for _ in self.pending]
        return [self.owner for _ in self.pending]


class _Adapter:
    def read_npm_position(self, token_id, owner=None):
        return PositionSnapshot(
            token_id=token_id,
            owner=owner or WALLET,
            pool_address=POOL_ADDRESS,
            token0=TOKEN0,
            token1=TOKEN1,
            fee=100,
            tick_lower=-100,
            tick_upper=100,
            liquidity=1000,
        )

    def read_staked_positions(self, token_ids):
        return {
            token_id: PositionSnapshot(
                token_id=token_id,
                owner=WALLET,
                pool_address=POOL_ADDRESS,
                token0=TOKEN0,
                token1=TOKEN1,
                fee=100,
                tick_lower=-100,
                tick_upper=100,
                liquidity=1000,
                is_staked=True,
            )
            for token_id in token_ids
        }


class _UnstakedAdapter(_Adapter):
    def read_staked_positions(self, _token_ids):
        return {}


def pool() -> PoolConfig:
    return PoolConfig(
        name="TEST",
        chain="BAS",
        pool_address=POOL_ADDRESS,
        dex_type=DexType.AERODROME_V3,
        managed_wallets=(WALLET,),
        bot_wallet=WALLET,
        npm_address=NPM_ADDRESS,
        token0_address=TOKEN0,
        token1_address=TOKEN1,
        tick_spacing=100,
        bootstrap_start_block=1,
    )


class CompoundPositionIndexTests(unittest.TestCase):
    def setUp(self):
        _FakeMulticall.calls = []
        _FakeMulticall.owner = WALLET

    def make_index(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return CompoundPositionIndex(directory.name)

    def test_enumerable_arguments_are_passed_as_one_tuple_and_owner_is_batched(self):
        index = self.make_index()
        index._db_candidates = lambda _pool: _DbCandidates(set(), set())
        with patch(
            "configured_pool_rebalancer.auto_compound.position_index.W3Multicall",
            _FakeMulticall,
        ):
            result = index.refresh(_W3(balance=1), pool(), _Adapter())

        self.assertIsInstance(result, CompoundDiscoveryResult)
        self.assertIn(42, result.positions)
        enumerable = next(call for call in _FakeMulticall.calls if call.signature.startswith("tokenOfOwnerByIndex"))
        self.assertEqual(enumerable.args, (WALLET, 0))
        owner_calls = [call for call in _FakeMulticall.calls if call.signature.startswith("ownerOf")]
        self.assertEqual(len(owner_calls), 1)

    def test_aerodrome_staked_hint_is_reported_as_policy_skip(self):
        index = self.make_index()
        index._db_candidates = lambda _pool: _DbCandidates(set(), set())
        _FakeMulticall.owner = OTHER
        with patch(
            "configured_pool_rebalancer.auto_compound.position_index.W3Multicall",
            _FakeMulticall,
        ):
            result = index.refresh(_W3(balance=0), pool(), _Adapter(), candidate_hints={77})

        self.assertEqual(result.positions, {})
        self.assertEqual([(item.token_id, item.reason) for item in result.policy_skips], [(77, "STAKE_POLICY")])

    def test_failed_transfer_chunk_does_not_mark_sweep_complete(self):
        index = self.make_index()

        class LogEth:
            def __init__(self):
                self.calls = 0

            def get_logs(self, _params):
                self.calls += 1
                if self.calls == 2:
                    raise RuntimeError("temporary RPC failure")
                return []

        w3 = type("W3", (), {"eth": LogEth()})()
        _, complete = index._sweep_transfers(w3, pool(), {"last_synced_block": 1}, 10)
        self.assertFalse(complete)

    def test_db_candidates_filter_unmanaged_staked_positions_before_rpc(self):
        snapshot = {
            "positions": {
                "1": {"pool_address": POOL_ADDRESS, "owner": WALLET, "is_staked": True},
                "2": {"pool_address": POOL_ADDRESS, "owner": OTHER, "is_staked": True},
                "3": {"pool_address": POOL_ADDRESS, "owner": WALLET, "is_staked": False},
                "4": {"pool_address": POOL_ADDRESS, "owner": OTHER, "is_staked": False},
            }
        }
        with patch(
            "configured_pool_rebalancer.auto_compound.position_index.fetch_position_cache_snapshot",
            return_value={"snapshot": snapshot},
        ):
            candidates = CompoundPositionIndex._db_candidates(pool())
        self.assertEqual(candidates.managed_staked, {1})
        self.assertEqual(candidates.unstaked, {3})

    def test_handoff_revalidation_does_not_run_full_discovery(self):
        index = self.make_index()
        index.refresh = lambda *args, **kwargs: self.fail("full discovery must not run")
        candidate = CompoundCandidate(
            chain="BAS",
            pool_name="TEST",
            pool_address=POOL_ADDRESS,
            wallet=WALLET,
            npm_address=NPM_ADDRESS,
            token_id=42,
            stake_mode=StakeMode.STAKED,
            anchor_block=99,
        )

        with patch(
            "configured_pool_rebalancer.auto_compound.position_index.W3Multicall",
            _FakeMulticall,
        ):
            result = index.revalidate_candidates(_W3(balance=1), pool(), _UnstakedAdapter(), (candidate,))

        self.assertIn(42, result.positions)
        self.assertFalse(result.positions[42].snapshot.is_staked)
        self.assertEqual(result.positions[42].stake_mode, "UNSTAKED")


if __name__ == "__main__":
    unittest.main()
