from __future__ import annotations

import unittest
from contextlib import contextmanager
from unittest.mock import Mock

from binance_futures_monitor.client import BinanceMonitorError
from binance_futures_monitor.models import (
    AccountConfig,
    BinanceCredentials,
    MarketType,
    MonitorConfig,
    RuntimeBinanceCredentials,
    WalletLink,
)
from binance_futures_monitor.worker import BinanceFuturesMonitor


class FakeRepository:
    def __init__(self):
        self.successes = []
        self.failures = []
        self.running = []
        self.mapping_syncs = 0

    def sync_wallet_links(self, config):
        self.mapping_syncs += 1

    @contextmanager
    def account_market_lock(self, account_alias, market):
        yield

    def mark_running(self, account_alias, market, stale_after_seconds):
        self.running.append((account_alias, market))

    def replace_positions_success(self, account_alias, market, stale_after_seconds, positions):
        self.successes.append((account_alias, market, positions))

    def mark_failed(self, account_alias, market, stale_after_seconds, code, message):
        self.failures.append((account_alias, market, code, message))


class LockBusyRepository(FakeRepository):
    @contextmanager
    def account_market_lock(self, account_alias, market):
        raise TimeoutError("busy")
        yield


class WorkerTests(unittest.TestCase):
    def test_market_failure_does_not_block_next_market_and_secrets_are_redacted(self):
        account = AccountConfig(
            "main",
            (MarketType.USD_M, MarketType.COIN_M),
            (WalletLink("EVM", "0x0000000000000000000000000000000000000001"),),
        )
        config = MonitorConfig((account,))
        runtime = RuntimeBinanceCredentials({
            "main": BinanceCredentials("api-key-value", "secret-key-value")
        })
        client = Mock()
        client.fetch_positions.side_effect = [
            BinanceMonitorError("AUTH_OR_IP", "bad secret-key-value", -2015),
            [],
        ]
        repository = FakeRepository()
        worker = BinanceFuturesMonitor(config, runtime, client=client, repository=repository)

        results = worker.run_once()

        self.assertEqual([item["status"] for item in results], ["FAILED", "SUCCESS"])
        self.assertEqual(len(repository.failures), 1)
        self.assertNotIn("secret-key-value", repository.failures[0][3])
        self.assertEqual(repository.successes[0][1], MarketType.COIN_M)
        self.assertEqual(repository.mapping_syncs, 1)

    def test_lock_busy_is_skipped_without_marking_account_failed(self):
        account = AccountConfig(
            "main",
            (MarketType.USD_M,),
            (WalletLink("EVM", "0x0000000000000000000000000000000000000001"),),
        )
        config = MonitorConfig((account,))
        runtime = RuntimeBinanceCredentials({
            "main": BinanceCredentials("api-key", "secret-key")
        })
        repository = LockBusyRepository()

        results = BinanceFuturesMonitor(
            config, runtime, client=Mock(), repository=repository
        ).run_once()

        self.assertEqual(results[0]["status"], "SKIPPED")
        self.assertEqual(results[0]["error_code"], "LOCK_BUSY")
        self.assertEqual(repository.failures, [])


if __name__ == "__main__":
    unittest.main()
