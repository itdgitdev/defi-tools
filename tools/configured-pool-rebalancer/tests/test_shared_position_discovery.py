from __future__ import annotations

import tempfile
import unittest
from unittest.mock import patch

from configured_pool_rebalancer.models import DexType, PoolConfig, PositionSnapshot
from configured_pool_rebalancer.position_index import PositionIndex


WALLET = "0x0000000000000000000000000000000000000002"
POOL = "0x0000000000000000000000000000000000000001"
NPM = "0x0000000000000000000000000000000000000003"
TOKEN0 = "0x0000000000000000000000000000000000000010"
TOKEN1 = "0x0000000000000000000000000000000000000020"


class _CallResult:
    def __init__(self, value):
        self.value = value

    def call(self):
        return self.value


class _Functions:
    def balanceOf(self, _wallet):
        return _CallResult(1)


class _Contract:
    functions = _Functions()


class _Eth:
    def get_block(self, _block):
        return {"number": 100}

    def contract(self, **_kwargs):
        return _Contract()


class _W3:
    eth = _Eth()


class _Multicall:
    calls = []

    class Call:
        def __init__(self, address, signature, args=None):
            self.address = address
            self.signature = signature
            self.args = args
            _Multicall.calls.append(self)

    def __init__(self, _w3):
        self.pending = []

    def add(self, call):
        self.pending.append(call)

    def call(self):
        if self.pending[0].signature.startswith("tokenOfOwnerByIndex"):
            return [42]
        return [WALLET]


class _MulticallWithInvalidOwner(_Multicall):
    invalid_token_id = 42

    def call(self):
        token_ids = [int(call.args) for call in self.pending]
        if self.invalid_token_id in token_ids:
            raise RuntimeError("execution reverted: Multicall3: call failed")
        return [WALLET for _ in token_ids]


class _Adapter:
    def read_staked_positions(self, _token_ids):
        return {}

    def read_npm_positions(self, token_ids, owners=None):
        return {
            token_id: PositionSnapshot(
                token_id=token_id,
                owner=(owners or {}).get(token_id, WALLET),
                pool_address=POOL,
                token0=TOKEN0,
                token1=TOKEN1,
                fee=100,
                tick_lower=-100,
                tick_upper=100,
                liquidity=1000,
            )
            for token_id in token_ids
        }

    def stake_event_contract_address(self):
        return None


def make_pool() -> PoolConfig:
    return PoolConfig(
        name="TEST",
        chain="BAS",
        pool_address=POOL,
        dex_type=DexType.AERODROME_V3,
        managed_wallets=(WALLET,),
        bot_wallet=WALLET,
        npm_address=NPM,
        token0_address=TOKEN0,
        token1_address=TOKEN1,
        tick_spacing=100,
    )


class SharedPositionDiscoveryTests(unittest.TestCase):
    def test_unstaked_position_is_discovered_once_and_validated_on_chain(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        index = PositionIndex(
            directory.name,
            use_legacy_cache=False,
            use_db_cache=False,
        )
        _Multicall.calls = []
        with patch(
            "configured_pool_rebalancer.position_index.W3Multicall",
            _Multicall,
        ):
            positions = index.refresh(_W3(), make_pool(), _Adapter())

        self.assertEqual(set(positions), {42})
        self.assertFalse(positions[42].is_staked)
        self.assertEqual(positions[42].last_updated_block, 100)
        enumerable = next(call for call in _Multicall.calls if call.signature.startswith("tokenOfOwnerByIndex"))
        self.assertEqual(enumerable.args, (WALLET, 0))
        self.assertEqual(len([call for call in _Multicall.calls if call.signature.startswith("ownerOf")]), 1)

    def test_failed_transfer_chunk_does_not_complete_checkpoint_sweep(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        index = PositionIndex(directory.name, use_legacy_cache=False, use_db_cache=False)

        class _LogEth:
            def __init__(self):
                self.calls = 0

            def get_logs(self, _params):
                self.calls += 1
                if self.calls == 2:
                    raise RuntimeError("temporary RPC error")
                return []

        w3 = type("W3", (), {"eth": _LogEth()})()
        _, complete = index._sweep_npm_transfer_logs(w3, make_pool(), 1, 10)
        self.assertFalse(complete)

    def test_owner_batch_revert_is_split_and_only_invalid_token_is_skipped(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        index = PositionIndex(directory.name, use_legacy_cache=False, use_db_cache=False)
        managed = {WALLET.lower()}

        with patch(
            "configured_pool_rebalancer.position_index.W3Multicall",
            _MulticallWithInvalidOwner,
        ):
            owned = index._batch_owned_token_ids(
                _W3(),
                make_pool(),
                {41, 42, 43},
                managed,
            )

        self.assertEqual(set(owned), {41, 43})


if __name__ == "__main__":
    unittest.main()
