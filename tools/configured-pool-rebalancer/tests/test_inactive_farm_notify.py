from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from configured_pool_rebalancer.adapter import PancakeV3MasterChefAdapter
from configured_pool_rebalancer.models import DexType, PoolConfig, PositionSnapshot, WorkerConfig
from configured_pool_rebalancer.worker import ConfiguredPoolRebalancer


TOKEN0 = "0x0000000000000000000000000000000000000003"
TOKEN1 = "0x0000000000000000000000000000000000000004"
WALLET = "0x0000000000000000000000000000000000000002"


def make_pool() -> PoolConfig:
    return PoolConfig(
        name="TEST-USDC-POP",
        chain="BASE",
        pool_address="0x0000000000000000000000000000000000000001",
        dex_type=DexType.PANCAKE_V3_MASTERCHEF,
        managed_wallets=(WALLET,),
        bot_wallet=WALLET,
        token0_address=TOKEN0,
        token1_address=TOKEN1,
        token0_decimals=6,
        token1_decimals=18,
        fee=2500,
        pid=1,
    )


def make_position(token_id: int = 123) -> PositionSnapshot:
    return PositionSnapshot(
        token_id=token_id,
        owner=WALLET,
        pool_address="0x0000000000000000000000000000000000000001",
        token0=TOKEN0,
        token1=TOKEN1,
        fee=2500,
        tick_lower=-100,
        tick_upper=100,
        liquidity=1000,
        pid=1,
        is_staked=True,
    )


class FakeNotifier:
    def __init__(self, enabled: bool = True):
        self._enabled = enabled
        self.sent: list[str] = []

    def enabled(self) -> bool:
        return self._enabled

    def send(self, message: str) -> None:
        self.sent.append(message)

    def inactive_farm_message(self, pool_name, chain, wallet, pid, alloc_point, token_ids):
        return f"inactive {pool_name} {chain} {wallet} {pid} {alloc_point} {token_ids}"


def make_adapter(alloc_point):
    adapter = PancakeV3MasterChefAdapter.__new__(PancakeV3MasterChefAdapter)
    adapter.read_farm_alloc_point = lambda: alloc_point
    return adapter


def make_worker(cache_dir: str, *, dry_run: bool = False, notifier_enabled: bool = True):
    worker = ConfiguredPoolRebalancer.__new__(ConfiguredPoolRebalancer)
    pool = make_pool()
    worker.config = WorkerConfig(
        pools=(pool,),
        cache_dir=cache_dir,
        dry_run=dry_run,
        discord_enabled=True,
    )
    worker.notifier = FakeNotifier(enabled=notifier_enabled)
    return worker, pool


class InactiveFarmNotifyTests(unittest.TestCase):
    def test_inactive_farm_sends_once_and_caches(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker, pool = make_worker(tmp)
            positions = {123: make_position(123)}
            adapter = make_adapter(0)

            worker._notify_inactive_farm_if_needed(pool, adapter, positions)
            worker._notify_inactive_farm_if_needed(pool, adapter, positions)

            self.assertEqual(len(worker.notifier.sent), 1)
            cache_path = Path(tmp) / "inactive_farm_notifications.json"
            self.assertTrue(cache_path.exists())

    def test_active_farm_resets_cache_and_allows_future_notify(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker, pool = make_worker(tmp)
            positions = {123: make_position(123)}

            worker._notify_inactive_farm_if_needed(pool, make_adapter(0), positions)
            worker._notify_inactive_farm_if_needed(pool, make_adapter(100), positions)
            worker._notify_inactive_farm_if_needed(pool, make_adapter(0), positions)

            self.assertEqual(len(worker.notifier.sent), 2)

    def test_no_notify_without_positions(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker, pool = make_worker(tmp)

            worker._notify_inactive_farm_if_needed(pool, make_adapter(0), {})

            self.assertEqual(worker.notifier.sent, [])

    def test_dry_run_does_not_notify(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker, pool = make_worker(tmp, dry_run=True)

            worker._notify_inactive_farm_if_needed(pool, make_adapter(0), {123: make_position(123)})

            self.assertEqual(worker.notifier.sent, [])

    def test_missing_alloc_point_does_not_notify(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker, pool = make_worker(tmp)

            worker._notify_inactive_farm_if_needed(pool, make_adapter(None), {123: make_position(123)})

            self.assertEqual(worker.notifier.sent, [])


if __name__ == "__main__":
    unittest.main()
