from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from configured_pool_rebalancer import cli
from configured_pool_rebalancer.models import AutoCompoundConfig, DexType, PoolConfig, WorkerConfig


class CompoundCliIntegrationTests(unittest.TestCase):
    @patch.object(cli, "ConfiguredPoolRebalancer")
    @patch.object(cli, "ConfiguredPoolAutomationWorker")
    @patch.object(cli, "prompt_runtime_signer", return_value=None)
    @patch.object(cli, "load_worker_config")
    def test_enabled_feature_selects_automation_wrapper(
        self, load_config, prompt_signer, automation_cls, rebalancer_cls
    ):
        pool = PoolConfig(
            name="TEST",
            chain="BAS",
            pool_address="0x0000000000000000000000000000000000000001",
            dex_type=DexType.PANCAKE_V3,
            managed_wallets=("0x0000000000000000000000000000000000000002",),
            bot_wallet="0x0000000000000000000000000000000000000002",
            auto_compound=AutoCompoundConfig(enabled=True),
        )
        load_config.return_value = WorkerConfig(pools=(pool,), dry_run=True)
        automation_cls.return_value.run_once.return_value = []

        result = cli.main([])

        self.assertEqual(result, 0)
        automation_cls.assert_called_once()
        rebalancer_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
