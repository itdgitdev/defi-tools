from __future__ import annotations

import unittest

from binance_futures_monitor.models import (
    AccountConfig,
    MarketType,
    MonitorConfig,
    WalletLink,
)
from binance_futures_monitor.repository import BinanceMonitorRepository


class FakeCursor:
    def __init__(self):
        self.executed = []
        self.executemany_calls = []
        self.fetchone_calls = 0

    def execute(self, query, params=None):
        self.executed.append((" ".join(query.split()), params))

    def fetchone(self):
        self.fetchone_calls += 1
        return (1,)

    def executemany(self, query, rows):
        self.executemany_calls.append((" ".join(query.split()), list(rows)))

    def close(self):
        pass


class FakeConnection:
    def __init__(self):
        self.cursor_instance = FakeCursor()
        self.commits = 0
        self.rollbacks = 0

    def cursor(self):
        return self.cursor_instance

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        pass


class RepositoryTests(unittest.TestCase):
    def test_wallet_mapping_is_replaced_under_advisory_lock(self):
        connection = FakeConnection()
        repository = BinanceMonitorRepository(lambda: connection)
        config = MonitorConfig((AccountConfig(
            "main",
            (MarketType.USD_M,),
            (WalletLink("EVM", "0x0000000000000000000000000000000000000001"),),
        ),))

        repository.sync_wallet_links(config)

        statements = [query for query, _ in connection.cursor_instance.executed]
        self.assertTrue(any("GET_LOCK" in query for query in statements))
        self.assertTrue(any("DELETE FROM binance_account_wallet_links" in query for query in statements))
        self.assertTrue(any("RELEASE_LOCK" in query for query in statements))
        self.assertEqual(connection.cursor_instance.fetchone_calls, 2)
        self.assertEqual(connection.commits, 1)

    def test_successful_empty_result_deletes_old_positions_and_marks_success(self):
        connection = FakeConnection()
        repository = BinanceMonitorRepository(lambda: connection)

        repository.replace_positions_success("main", MarketType.COIN_M, 180, [])

        statements = [query for query, _ in connection.cursor_instance.executed]
        self.assertTrue(any("DELETE FROM binance_futures_positions_current" in query for query in statements))
        self.assertTrue(any("SET status = 'SUCCESS'" in query for query in statements))
        self.assertEqual(connection.cursor_instance.executemany_calls, [])
        self.assertEqual(connection.commits, 1)
        self.assertEqual(connection.rollbacks, 0)


if __name__ == "__main__":
    unittest.main()
