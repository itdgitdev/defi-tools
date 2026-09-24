from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from configured_pool_rebalancer.auto_compound.executor import CompoundExecutor
from configured_pool_rebalancer.auto_compound.models import CompoundJobState
from configured_pool_rebalancer.models import DexType, PoolConfig, WorkerConfig


class FakeCall:
    def estimate_gas(self, tx):
        return 100_000

    def build_transaction(self, tx):
        return {**tx, "to": "0x0000000000000000000000000000000000000005", "data": "0x1234"}


class FakeEth:
    def __init__(self, events):
        self.events = events

    def get_transaction_count(self, wallet, state):
        return 7

    def send_raw_transaction(self, raw):
        self.events.append("broadcast")
        return bytes.fromhex("12" * 32)

    def wait_for_transaction_receipt(self, tx_hash, timeout):
        return {"status": 1, "gasUsed": 100, "effectiveGasPrice": 50_000_000, "blockNumber": 10, "logs": []}


class FakeW3:
    def __init__(self, events):
        self.eth = FakeEth(events)
        self.provider = SimpleNamespace(endpoint_uri="http://primary")


class FakeSigner:
    def sign_transaction(self, wallet, tx):
        return SimpleNamespace(raw_transaction=b"compound-signed")


class FakeJournal:
    def __init__(self, events):
        self.events = events

    def mark_pending(self, *args):
        self.events.append("pending-persisted")

    def record_broadcast(self, *args):
        self.events.append("broadcast-persisted")

    def complete_transaction(self, *args):
        self.events.append("completed")


class CompoundExecutorTests(unittest.TestCase):
    def test_signed_hash_is_persisted_before_broadcast(self):
        events = []
        pool = PoolConfig(
            name="TEST",
            chain="BNB",
            pool_address="0x0000000000000000000000000000000000000001",
            dex_type=DexType.PANCAKE_V3,
            managed_wallets=("0x0000000000000000000000000000000000000002",),
            bot_wallet="0x0000000000000000000000000000000000000002",
        )
        executor = CompoundExecutor(
            FakeW3(events), pool, WorkerConfig(pools=(pool,), dry_run=False), FakeJournal(events), FakeSigner(),
            write_w3=FakeW3(events), tx_guard=Mock()
        )
        executor._rpc_attempts = lambda: [("primary", executor.w3)]

        result = executor.send_call(
            1, FakeCall(), "COLLECT", CompoundJobState.COLLECT_PENDING,
            CompoundJobState.COLLECTED, "collect_tx_hash",
        )

        self.assertEqual(result.status, "SUCCESS")
        self.assertLess(events.index("pending-persisted"), events.index("broadcast"))
        self.assertEqual(events[-2:], ["broadcast-persisted", "completed"])


if __name__ == "__main__":
    unittest.main()
