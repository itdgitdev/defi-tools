from __future__ import annotations

import unittest

from configured_pool_rebalancer.automation_worker import ConfiguredPoolAutomationWorker
from configured_pool_rebalancer.models import (
    AutoCompoundConfig,
    CompoundCandidate,
    DexType,
    PoolConfig,
    RebalanceCycleOutcome,
    StakeMode,
    WorkerConfig,
)


WALLET = "0x0000000000000000000000000000000000000002"


def config() -> WorkerConfig:
    pool = PoolConfig(
        name="TEST",
        chain="BAS",
        pool_address="0x0000000000000000000000000000000000000001",
        dex_type=DexType.PANCAKE_V3,
        managed_wallets=(WALLET,),
        bot_wallet=WALLET,
        auto_compound=AutoCompoundConfig(enabled=True),
    )
    return WorkerConfig(pools=(pool,), dry_run=True)


class FakeRebalancer:
    def __init__(self, results):
        self.results = results

    def run_once_with_outcome(self):
        candidates = {}
        blocked = set()
        for result in self.results:
            if result.get("state") == "IN_RANGE" and result.get("token_id") is not None:
                candidates.setdefault("TEST", []).append(
                    CompoundCandidate(
                        chain="BAS",
                        pool_name="TEST",
                        pool_address="0x0000000000000000000000000000000000000001",
                        wallet=WALLET,
                        npm_address="0x0000000000000000000000000000000000000003",
                        token_id=int(result["token_id"]),
                        stake_mode=StakeMode.UNSTAKED,
                    )
                )
            else:
                blocked.add(WALLET.lower())
        return RebalanceCycleOutcome(
            records=list(self.results),
            compound_candidates={key: tuple(value) for key, value in candidates.items()},
            blocked_wallets=blocked,
        )


class FakeCompounder:
    def __init__(self):
        self.blocked = None
        self.compound_candidates = None

    def reconcile_pending_wallets(self):
        return set()

    def run_once(self, blocked_wallets=None, compound_candidates=None):
        self.blocked = blocked_wallets
        self.compound_candidates = compound_candidates
        return [{"action": "COMPOUND", "state": "DONE"}]


class AutomationWorkerTests(unittest.TestCase):
    def make_worker(self, rebalance_results):
        worker = ConfiguredPoolAutomationWorker.__new__(ConfiguredPoolAutomationWorker)
        worker.config = config()
        worker.signer = None
        worker.rebalancer = FakeRebalancer(rebalance_results)
        worker.compounder = FakeCompounder()
        return worker

    def test_in_range_result_does_not_block_compound(self):
        worker = self.make_worker([{"pool": "TEST", "state": "IN_RANGE", "token_id": 42}])
        worker.run_once()
        self.assertEqual(worker.compounder.blocked, set())
        self.assertEqual(worker.compounder.compound_candidates["TEST"][0].token_id, 42)

    def test_planned_rebalance_blocks_same_wallet_compound(self):
        worker = self.make_worker([{"pool": "TEST", "state": "PLANNED"}])
        worker.run_once()
        self.assertEqual(worker.compounder.blocked, {WALLET.lower()})

    def test_rebalance_error_blocks_same_wallet_compound(self):
        worker = self.make_worker([{"pool": "TEST", "status": "ERROR"}])
        worker.run_once()
        self.assertEqual(worker.compounder.blocked, {WALLET.lower()})


if __name__ == "__main__":
    unittest.main()
