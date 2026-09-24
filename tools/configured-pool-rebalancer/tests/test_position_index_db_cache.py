from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from configured_pool_rebalancer.models import DexType, PoolConfig, PositionSnapshot
from configured_pool_rebalancer.position_index import DEPOSIT_TOPIC, PositionIndex


TOKEN0 = "0x0000000000000000000000000000000000000003"
TOKEN1 = "0x0000000000000000000000000000000000000004"
WALLET = "0x0000000000000000000000000000000000000002"


def make_pool() -> PoolConfig:
    return PoolConfig(
        name="TEST",
        chain="BNB",
        pool_address="0x0000000000000000000000000000000000000001",
        dex_type=DexType.PANCAKE_V3_MASTERCHEF,
        managed_wallets=(WALLET,),
        bot_wallet=WALLET,
        token0_address=TOKEN0,
        token1_address=TOKEN1,
        token0_decimals=18,
        token1_decimals=18,
        fee=2500,
        pid=510,
    )


def make_aerodrome_pool() -> PoolConfig:
    return PoolConfig(
        name="AERO",
        chain="BAS",
        pool_address="0x0000000000000000000000000000000000000001",
        dex_type=DexType.AERODROME_V3,
        managed_wallets=(WALLET,),
        bot_wallet=WALLET,
        token0_address=TOKEN0,
        token1_address=TOKEN1,
        token0_decimals=18,
        token1_decimals=18,
        fee=100,
        tick_spacing=100,
        pid=None,
        staking_address="0x0000000000000000000000000000000000000005",
        auto_bootstrap_start_block=False,
        seed_token_ids=(1001,),
    )


def make_position(token_id: int) -> PositionSnapshot:
    return PositionSnapshot(
        token_id=token_id,
        owner=WALLET,
        pool_address="0x0000000000000000000000000000000000000001",
        token0=TOKEN0,
        token1=TOKEN1,
        fee=2500,
        tick_lower=1,
        tick_upper=2,
        liquidity=100,
        pid=510,
        is_staked=True,
    )


class FakeEth:
    def __init__(self, latest_block: int):
        self.latest_block = latest_block
        self.get_logs_calls = []

    def get_block(self, block):
        return {"number": self.latest_block}

    def get_logs(self, params):
        self.get_logs_calls.append(params)
        return []


class FakeW3:
    def __init__(self, latest_block: int):
        self.eth = FakeEth(latest_block)


class FailingLogsEth(FakeEth):
    def get_logs(self, params):
        self.get_logs_calls.append(params)
        raise RuntimeError("primary get_logs failed")


class LogsEth(FakeEth):
    def __init__(self, latest_block: int, logs):
        super().__init__(latest_block)
        self.logs = logs

    def get_logs(self, params):
        self.get_logs_calls.append(params)
        return list(self.logs)


class CustomW3:
    def __init__(self, eth):
        self.eth = eth


class FakeAdapter:
    masterchef_address = "0x0000000000000000000000000000000000000005"

    def __init__(self):
        self.seen_token_ids = None

    def read_staked_positions(self, token_ids):
        self.seen_token_ids = set(token_ids)
        return {1001: make_position(1001)} if 1001 in self.seen_token_ids else {}


class PositionIndexDbCacheTests(unittest.TestCase):
    def test_db_snapshot_supplies_candidates_and_incremental_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool = make_pool()
            index = PositionIndex(tmp, use_legacy_cache=False, use_db_cache=True)
            adapter = FakeAdapter()
            snapshot = {
                "snapshot": {
                    "last_synced_block": 100,
                    "bootstrapped_pids": [510],
                    "positions": {
                        "1001": {"pid": 510, "liquidity": 100},
                        "1002": {"pid": 511, "liquidity": 100},
                    },
                },
                "last_synced_block": 100,
                "position_count": 2,
            }

            with patch(
                "configured_pool_rebalancer.position_index.fetch_position_cache_snapshot",
                return_value=snapshot,
            ):
                positions = index.refresh(FakeW3(100), pool, adapter)

            self.assertEqual(set(positions), {1001})
            self.assertEqual(adapter.seen_token_ids, {1001})
            cache = json.loads((Path(tmp) / "BNB_0x0000000000000000000000000000000000000001.json").read_text())
            self.assertEqual(cache["sync"]["source"], "db_position_cache")
            self.assertEqual(cache["sync"]["db_block"], 100)
            self.assertEqual(cache["db_bootstrap"]["candidate_count"], 1)

    def test_pidless_pool_ignores_legacy_pid_only_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool = make_aerodrome_pool()
            index = PositionIndex(tmp, use_legacy_cache=False, use_db_cache=True)
            adapter = FakeAdapter()
            snapshot = {
                "snapshot": {
                    "last_synced_block": 100,
                    "positions": {
                        "1002": {"pid": 465, "liquidity": 100},
                        "1003": {"pid": 466, "liquidity": 100},
                    },
                },
                "last_synced_block": 100,
                "position_count": 2,
            }

            with patch(
                "configured_pool_rebalancer.position_index.fetch_position_cache_snapshot",
                return_value=snapshot,
            ):
                positions = index.refresh(FakeW3(100), pool, adapter)

            self.assertEqual(set(positions), {1001})
            self.assertEqual(adapter.seen_token_ids, {1001})
            cache = json.loads((Path(tmp) / "BAS_0x0000000000000000000000000000000000000001.json").read_text())
            self.assertEqual(cache["sync"]["source"], "skip_historical")
            self.assertEqual(cache["db_bootstrap"]["candidate_count"], 0)

    def test_aerodrome_db_snapshot_uses_aerodrome_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool = make_aerodrome_pool()
            index = PositionIndex(tmp, use_legacy_cache=False, use_db_cache=True)
            snapshot = {
                "snapshot": {
                    "last_synced_block": 100,
                    "positions": {
                        "1001": {
                            "pool_address": pool.pool_address,
                            "gauge_address": pool.staking_address,
                            "liquidity": "100",
                        },
                    },
                },
                "last_synced_block": 100,
                "position_count": 1,
            }

            with patch(
                "configured_pool_rebalancer.position_index.fetch_position_cache_snapshot",
                return_value=snapshot,
            ) as fetch:
                index.refresh(FakeW3(100), pool, FakeAdapter())

            fetch.assert_called_once_with("BAS", "aerodrome_positions_cache")

    def test_aerodrome_empty_bootstrapped_snapshot_avoids_historical_sweep(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool = make_aerodrome_pool()
            pool = PoolConfig(**{**pool.__dict__, "auto_bootstrap_start_block": True, "seed_token_ids": ()})
            index = PositionIndex(tmp, use_legacy_cache=False, use_db_cache=True)
            w3 = FakeW3(100)
            snapshot = {
                "snapshot": {
                    "last_synced_block": 100,
                    "bootstrapped_pools": [pool.pool_address.lower()],
                    "positions": {},
                },
                "last_synced_block": 100,
                "position_count": 0,
            }

            with patch(
                "configured_pool_rebalancer.position_index.fetch_position_cache_snapshot",
                return_value=snapshot,
            ):
                positions = index.refresh(w3, pool, FakeAdapter())

            self.assertEqual(positions, {})
            self.assertFalse(any(call["address"] == pool.staking_address for call in w3.eth.get_logs_calls))

    def test_pancake_db_snapshot_keeps_configured_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool = make_pool()
            index = PositionIndex(tmp, use_legacy_cache=False, use_db_cache=True, db_cache_source="positions_cache")
            snapshot = {
                "snapshot": {"last_synced_block": 100, "bootstrapped_pids": [510], "positions": {}},
                "last_synced_block": 100,
                "position_count": 0,
            }

            with patch(
                "configured_pool_rebalancer.position_index.fetch_position_cache_snapshot",
                return_value=snapshot,
            ) as fetch:
                index.refresh(FakeW3(100), pool, FakeAdapter())

            fetch.assert_called_once_with("BNB", "positions_cache")

    def test_db_snapshot_bootstrapped_empty_pid_avoids_historical_sweep(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool = make_pool()
            index = PositionIndex(tmp, use_legacy_cache=False, use_db_cache=True)
            w3 = FakeW3(100)
            snapshot = {
                "snapshot": {"last_synced_block": 100, "bootstrapped_pids": [510], "positions": {}},
                "last_synced_block": 100,
                "position_count": 0,
            }

            with patch(
                "configured_pool_rebalancer.position_index.fetch_position_cache_snapshot",
                return_value=snapshot,
            ):
                positions = index.refresh(w3, pool, FakeAdapter())

            self.assertEqual(positions, {})
            self.assertEqual(w3.eth.get_logs_calls, [])

    def test_existing_module_cache_skips_db_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool = make_pool()
            path = Path(tmp) / "BNB_0x0000000000000000000000000000000000000001.json"
            path.write_text(
                json.dumps(
                    {
                        "last_synced_block": 100,
                        "token_ids": [],
                        "positions": {},
                    }
                ),
                encoding="utf-8",
            )
            index = PositionIndex(tmp, use_legacy_cache=False, use_db_cache=True)

            with patch(
                "configured_pool_rebalancer.position_index.fetch_position_cache_snapshot",
            ) as fetch:
                index.refresh(FakeW3(100), pool, FakeAdapter())

            fetch.assert_not_called()

    def test_aerodrome_newer_db_snapshot_advances_stale_module_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool = make_aerodrome_pool()
            path = Path(tmp) / "BAS_0x0000000000000000000000000000000000000001.json"
            path.write_text(
                json.dumps(
                    {
                        "last_synced_block": 10,
                        "token_ids": [],
                        "positions": {},
                    }
                ),
                encoding="utf-8",
            )
            index = PositionIndex(tmp, use_legacy_cache=False, use_db_cache=True)
            snapshot = {
                "snapshot": {
                    "last_synced_block": 150,
                    "bootstrapped_pools": [pool.pool_address],
                    "positions": {
                        "1001": {
                            "pool_address": pool.pool_address,
                            "gauge_address": pool.staking_address,
                        }
                    },
                },
                "last_synced_block": 150,
                "position_count": 1,
            }
            w3 = FakeW3(151)

            with patch(
                "configured_pool_rebalancer.position_index.fetch_position_cache_snapshot",
                return_value=snapshot,
            ) as fetch:
                positions = index.refresh(w3, pool, FakeAdapter())

            fetch.assert_called_once_with("BAS", "aerodrome_positions_cache")
            self.assertEqual(set(positions), {1001})
            self.assertEqual(w3.eth.get_logs_calls, [])

    def test_masterchef_log_sweep_uses_fallback_rpc_when_primary_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            pool = make_pool()
            index = PositionIndex(tmp, use_legacy_cache=False, use_db_cache=True)
            primary = CustomW3(FailingLogsEth(100))
            token_id = 1001
            event = {
                "topics": [
                    bytes.fromhex(DEPOSIT_TOPIC[2:]),
                    b"\x00" * 32,
                    b"\x00" * 32,
                    int(token_id).to_bytes(32, "big"),
                ]
            }
            fallback = CustomW3(LogsEth(100, [event]))
            adapter = FakeAdapter()
            snapshot = {
                "snapshot": {"last_synced_block": 99, "bootstrapped_pids": [510], "positions": {}},
                "last_synced_block": 99,
                "position_count": 0,
            }

            with patch(
                "configured_pool_rebalancer.position_index.fetch_position_cache_snapshot",
                return_value=snapshot,
            ), patch.object(index, "_log_rpc_sources", return_value=[("primary", primary), ("fallback-test", fallback)]), patch(
                "configured_pool_rebalancer.position_index.time.sleep",
            ):
                positions = index.refresh(primary, pool, adapter)

            self.assertEqual(set(positions), {token_id})
            self.assertEqual(len(primary.eth.get_logs_calls), 2)
            self.assertEqual(len(fallback.eth.get_logs_calls), 1)


if __name__ == "__main__":
    unittest.main()
