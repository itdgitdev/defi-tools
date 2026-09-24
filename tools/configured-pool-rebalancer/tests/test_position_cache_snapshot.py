from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from configured_pool_rebalancer.position_cache_snapshot import push_position_cache_snapshot


class FakeCursor:
    def __init__(self, existing_hash=None):
        self.existing_hash = existing_hash
        self.queries = []
        self.closed = False

    def execute(self, query, params=None):
        self.queries.append((query, params))

    def fetchone(self):
        return (self.existing_hash,) if self.existing_hash else None

    def close(self):
        self.closed = True


class FakeConnection:
    def __init__(self, cursor):
        self.cursor_obj = cursor
        self.commits = 0
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def commit(self):
        self.commits += 1

    def close(self):
        self.closed = True


class PositionCacheSnapshotTests(unittest.TestCase):
    def _write_cache(self, cache_dir: Path, chain: str = "BNB"):
        payload = {
            "last_synced_block": 123,
            "bootstrapped_pids": [510],
            "positions": {
                "1001": {"pid": 510, "liquidity": 1, "tick_lower": 1, "tick_upper": 2},
                "1002": {"pid": 511, "liquidity": 1, "tick_lower": 3, "tick_upper": 4},
            },
        }
        path = cache_dir / f"positions_cache_{chain}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_push_snapshot_upserts_valid_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_cache(Path(tmp))
            cursor = FakeCursor()
            connection = FakeConnection(cursor)

            with patch(
                "configured_pool_rebalancer.position_cache_snapshot.get_connection",
                return_value=connection,
            ):
                result = push_position_cache_snapshot("BNB", cache_dir=tmp)

            self.assertEqual(result["status"], "updated")
            self.assertEqual(result["last_synced_block"], 123)
            self.assertEqual(result["position_count"], 2)
            self.assertTrue(any("INSERT INTO configured_position_cache_snapshots" in query for query, _ in cursor.queries))
            self.assertEqual(connection.commits, 1)
            self.assertTrue(cursor.closed)
            self.assertTrue(connection.closed)

    def test_push_snapshot_accepts_custom_cache_path_and_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            payload = {
                "last_synced_block": 456,
                "bootstrapped_pools": ["0x0000000000000000000000000000000000000001"],
                "positions": {
                    "1001": {
                        "pool_address": "0x0000000000000000000000000000000000000001",
                        "gauge_address": "0x0000000000000000000000000000000000000005",
                        "liquidity": "1",
                    }
                },
            }
            path = Path(tmp) / "aerodrome_positions_cache_BAS.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            cursor = FakeCursor()
            connection = FakeConnection(cursor)

            with patch(
                "configured_pool_rebalancer.position_cache_snapshot.get_connection",
                return_value=connection,
            ):
                result = push_position_cache_snapshot(
                    "BAS",
                    source="aerodrome_positions_cache",
                    cache_path=path,
                )

            self.assertEqual(result["status"], "updated")
            self.assertEqual(result["last_synced_block"], 456)
            self.assertEqual(result["position_count"], 1)
            self.assertTrue(
                any(params and params[1] == "aerodrome_positions_cache" for _, params in cursor.queries)
            )

    def test_push_snapshot_skips_when_hash_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_cache(Path(tmp))
            import hashlib

            existing_hash = hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
            cursor = FakeCursor(existing_hash=existing_hash)
            connection = FakeConnection(cursor)

            with patch(
                "configured_pool_rebalancer.position_cache_snapshot.get_connection",
                return_value=connection,
            ):
                result = push_position_cache_snapshot("BNB", cache_dir=tmp)

            self.assertEqual(result["status"], "skipped")
            self.assertFalse(any("INSERT INTO configured_position_cache_snapshots" in query for query, _ in cursor.queries))
            self.assertEqual(connection.commits, 1)

    def test_push_snapshot_rejects_invalid_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "positions_cache_BNB.json"
            path.write_text(json.dumps({"last_synced_block": 1, "positions": []}), encoding="utf-8")

            with self.assertRaises(ValueError):
                push_position_cache_snapshot("BNB", cache_dir=tmp)


if __name__ == "__main__":
    unittest.main()
