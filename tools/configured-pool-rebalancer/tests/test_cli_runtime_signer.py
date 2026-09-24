from __future__ import annotations

import json
import os
import unittest
from unittest.mock import Mock, patch

from eth_account import Account
from web3 import Web3

from configured_pool_rebalancer import cli
from configured_pool_rebalancer.models import DexType, PoolConfig, WorkerConfig


PRIVATE_KEY = "0x" + "1" * 64
PRIVATE_KEY_HEX = PRIVATE_KEY.removeprefix("0x")
PREFIX = PRIVATE_KEY_HEX[:54]
SUFFIX = PRIVATE_KEY_HEX[-10:]
WALLET = Web3.to_checksum_address(Account.from_key(PRIVATE_KEY).address)

OTHER_KEY = "0x" + "2" * 64
OTHER_KEY_HEX = OTHER_KEY.removeprefix("0x")
OTHER_PREFIX = OTHER_KEY_HEX[:54]
OTHER_SUFFIX = OTHER_KEY_HEX[-10:]
OTHER_WALLET = Web3.to_checksum_address(Account.from_key(OTHER_KEY).address)

PREFIX_ENV = "TEST_CONFIGURED_REBALANCER_PRIVATE_KEY_PREFIX"
OTHER_PREFIX_ENV = "TEST_CONFIGURED_REBALANCER_OTHER_PRIVATE_KEY_PREFIX"
POOL_ADDRESS = "0x0000000000000000000000000000000000000001"


def make_pool(name="TEST", *, wallet=WALLET, prefix_env=PREFIX_ENV):
    return PoolConfig(
        name=name,
        chain="BAS",
        pool_address=POOL_ADDRESS,
        dex_type=DexType.PANCAKE_V3_MASTERCHEF,
        managed_wallets=(wallet,),
        bot_wallet=wallet,
        private_key_prefix_env=prefix_env,
    )


class CliRuntimeSignerTests(unittest.TestCase):
    def test_dry_run_prompt_runtime_signer_returns_none_without_env_or_prompt(self):
        config = WorkerConfig(pools=(make_pool(),), dry_run=True)

        with (
            patch.dict(os.environ, {}, clear=True),
            patch("configured_pool_rebalancer.cli.getpass.getpass") as getpass_mock,
        ):
            signer = cli.prompt_runtime_signer(config)

        self.assertIsNone(signer)
        getpass_mock.assert_not_called()

    def test_execute_reconstructs_key_and_prompts_once_per_distinct_wallet(self):
        config = WorkerConfig(pools=(make_pool("A"), make_pool("B")), dry_run=False)

        with (
            patch.dict(os.environ, {PREFIX_ENV: PREFIX}, clear=True),
            patch(
                "configured_pool_rebalancer.cli.getpass.getpass",
                return_value=SUFFIX,
            ) as getpass_mock,
        ):
            signer = cli.prompt_runtime_signer(config)

        self.assertIsNotNone(signer)
        self.assertEqual(getpass_mock.call_count, 1)
        self.assertIn("remaining 10", getpass_mock.call_args.args[0])

    def test_execute_supports_distinct_prefix_env_per_wallet(self):
        config = WorkerConfig(
            pools=(
                make_pool("A"),
                make_pool("B", wallet=OTHER_WALLET, prefix_env=OTHER_PREFIX_ENV),
            ),
            dry_run=False,
        )

        with (
            patch.dict(
                os.environ,
                {
                    PREFIX_ENV: PREFIX,
                    OTHER_PREFIX_ENV: OTHER_PREFIX,
                },
                clear=True,
            ),
            patch(
                "configured_pool_rebalancer.cli.getpass.getpass",
                side_effect=[SUFFIX, OTHER_SUFFIX],
            ) as getpass_mock,
        ):
            signer = cli.prompt_runtime_signer(config)

        self.assertIsNotNone(signer)
        self.assertEqual(getpass_mock.call_count, 2)

    def test_missing_prefix_fails_before_prompt(self):
        config = WorkerConfig(pools=(make_pool(),), dry_run=False)

        with (
            patch.dict(os.environ, {}, clear=True),
            patch("configured_pool_rebalancer.cli.getpass.getpass") as getpass_mock,
        ):
            with self.assertRaisesRegex(ValueError, PREFIX_ENV):
                cli.prompt_runtime_signer(config)

        getpass_mock.assert_not_called()

    def test_all_prefixes_are_validated_before_prompt(self):
        config = WorkerConfig(
            pools=(
                make_pool("A"),
                make_pool("B", wallet=OTHER_WALLET, prefix_env=OTHER_PREFIX_ENV),
            ),
            dry_run=False,
        )

        with (
            patch.dict(os.environ, {PREFIX_ENV: PREFIX}, clear=True),
            patch("configured_pool_rebalancer.cli.getpass.getpass") as getpass_mock,
        ):
            with self.assertRaisesRegex(ValueError, OTHER_PREFIX_ENV):
                cli.prompt_runtime_signer(config)

        getpass_mock.assert_not_called()

    def test_invalid_prefix_fails_without_leaking_value(self):
        config = WorkerConfig(pools=(make_pool(),), dry_run=False)

        for invalid_prefix in ("1" * 64, "z" * 54):
            with self.subTest(invalid_prefix_length=len(invalid_prefix)):
                with (
                    patch.dict(os.environ, {PREFIX_ENV: invalid_prefix}, clear=True),
                    patch("configured_pool_rebalancer.cli.getpass.getpass") as getpass_mock,
                ):
                    with self.assertRaises(ValueError) as context:
                        cli.prompt_runtime_signer(config)

                self.assertNotIn(invalid_prefix, str(context.exception))
                getpass_mock.assert_not_called()

    def test_conflicting_prefix_env_for_same_wallet_fails_before_prompt(self):
        config = WorkerConfig(
            pools=(make_pool("A"), make_pool("B", prefix_env=OTHER_PREFIX_ENV)),
            dry_run=False,
        )

        with patch("configured_pool_rebalancer.cli.getpass.getpass") as getpass_mock:
            with self.assertRaisesRegex(ValueError, "conflicting private_key_prefix_env"):
                cli.prompt_runtime_signer(config)

        getpass_mock.assert_not_called()

    def test_empty_prefix_env_name_fails_before_prompt(self):
        config = WorkerConfig(pools=(make_pool(prefix_env=""),), dry_run=False)

        with patch("configured_pool_rebalancer.cli.getpass.getpass") as getpass_mock:
            with self.assertRaisesRegex(ValueError, "private_key_prefix_env is required"):
                cli.prompt_runtime_signer(config)

        getpass_mock.assert_not_called()

    def test_invalid_suffixes_fail_without_leaking_values(self):
        config = WorkerConfig(pools=(make_pool(),), dry_run=False)

        for invalid_suffix in ("", "1" * 9, "1" * 11, "z" * 10, "0x" + "1" * 8):
            with self.subTest(invalid_suffix=invalid_suffix):
                with (
                    patch.dict(os.environ, {PREFIX_ENV: PREFIX}, clear=True),
                    patch(
                        "configured_pool_rebalancer.cli.getpass.getpass",
                        return_value=invalid_suffix,
                    ) as getpass_mock,
                ):
                    with self.assertRaisesRegex(ValueError, "after 3 attempts") as context:
                        cli.prompt_runtime_signer(config)

                if invalid_suffix:
                    self.assertNotIn(invalid_suffix, str(context.exception))
                self.assertEqual(getpass_mock.call_count, 3)

    def test_pnl_report_loads_dotenv_but_does_not_read_key_or_prompt(self):
        config = WorkerConfig(pools=(make_pool(),), dry_run=False)

        with (
            patch("configured_pool_rebalancer.cli.load_dotenv") as dotenv_mock,
            patch("configured_pool_rebalancer.cli.load_worker_config", return_value=config),
            patch("configured_pool_rebalancer.cli.getpass.getpass") as getpass_mock,
            patch("configured_pool_rebalancer.cli.ConfiguredPoolPnlReporter") as reporter_cls,
            patch("builtins.print"),
        ):
            reporter_cls.return_value.write_report.return_value.records = []
            reporter_cls.return_value.write_report.return_value.written_files = []
            result = cli.main(["--execute", "--pnl-report"])

        self.assertEqual(result, 0)
        dotenv_mock.assert_called_once_with(cli.ENV_FILE, override=False)
        getpass_mock.assert_not_called()

    def test_execute_key_mismatch_fails_before_worker_runs(self):
        config = WorkerConfig(pools=(make_pool(),), dry_run=False)

        with (
            patch.dict(os.environ, {PREFIX_ENV: OTHER_PREFIX}, clear=True),
            patch("configured_pool_rebalancer.cli.load_dotenv"),
            patch("configured_pool_rebalancer.cli.load_worker_config", return_value=config),
            patch(
                "configured_pool_rebalancer.cli.getpass.getpass",
                return_value=OTHER_SUFFIX,
            ),
            patch("configured_pool_rebalancer.cli.ConfiguredPoolRebalancer") as worker_cls,
        ):
            with self.assertRaisesRegex(ValueError, "after 3 attempts"):
                cli.main(["--execute"])

        worker_cls.assert_not_called()

    def test_non_loop_runs_once(self):
        config = WorkerConfig(pools=(make_pool(),), dry_run=True)
        worker = Mock()
        worker.run_once.return_value = [{"state": "IN_RANGE"}]

        with (
            patch("configured_pool_rebalancer.cli.load_dotenv") as dotenv_mock,
            patch("configured_pool_rebalancer.cli.load_worker_config", return_value=config),
            patch("configured_pool_rebalancer.cli.ConfiguredPoolRebalancer", return_value=worker),
            patch("builtins.print"),
        ):
            result = cli.main([])

        self.assertEqual(result, 0)
        dotenv_mock.assert_called_once_with(cli.ENV_FILE, override=False)
        worker.run_once.assert_called_once()

    def test_loop_dry_run_does_not_prompt_and_runs_multiple_cycles(self):
        config = WorkerConfig(pools=(make_pool(),), dry_run=True, interval_seconds=0)
        worker = Mock()
        worker.run_once.side_effect = [[{"cycle": 1}], [{"cycle": 2}], KeyboardInterrupt()]

        with (
            patch("configured_pool_rebalancer.cli.getpass.getpass") as getpass_mock,
            patch("builtins.print") as print_mock,
        ):
            result = cli.run_loop(worker, config)

        self.assertEqual(result, 0)
        getpass_mock.assert_not_called()
        self.assertEqual(worker.run_once.call_count, 3)
        self.assertEqual(print_mock.call_count, 2)
        payload = json.loads(print_mock.call_args_list[0].args[0])
        self.assertEqual(payload["cycle"], 1)
        self.assertEqual(payload["interval_seconds"], 0)
        self.assertEqual(payload["status"], "SUCCESS")
        self.assertTrue(print_mock.call_args_list[0].kwargs["flush"])

    def test_loop_execute_prompts_once_and_reuses_worker_signer(self):
        config = WorkerConfig(pools=(make_pool("A"), make_pool("B")), dry_run=False, interval_seconds=0)
        worker = Mock()
        worker.run_once.side_effect = [[{"cycle": 1}], KeyboardInterrupt()]

        with (
            patch.dict(os.environ, {PREFIX_ENV: PREFIX}, clear=True),
            patch("configured_pool_rebalancer.cli.load_dotenv"),
            patch("configured_pool_rebalancer.cli.load_worker_config", return_value=config),
            patch(
                "configured_pool_rebalancer.cli.getpass.getpass",
                return_value=SUFFIX,
            ) as getpass_mock,
            patch(
                "configured_pool_rebalancer.cli.ConfiguredPoolRebalancer",
                return_value=worker,
            ) as worker_cls,
            patch("builtins.print"),
        ):
            result = cli.main(["--execute", "--loop"])

        self.assertEqual(result, 0)
        self.assertEqual(getpass_mock.call_count, 1)
        worker_cls.assert_called_once()
        self.assertIsNotNone(worker_cls.call_args.kwargs["signer"])
        self.assertEqual(worker.run_once.call_count, 2)

    def test_loop_migrate_is_used_only_for_worker_construction(self):
        config = WorkerConfig(pools=(make_pool(),), dry_run=True, interval_seconds=0)
        worker = Mock()
        worker.run_once.side_effect = [[{"cycle": 1}], KeyboardInterrupt()]

        with (
            patch("configured_pool_rebalancer.cli.load_dotenv"),
            patch("configured_pool_rebalancer.cli.load_worker_config", return_value=config),
            patch(
                "configured_pool_rebalancer.cli.ConfiguredPoolRebalancer",
                return_value=worker,
            ) as worker_cls,
            patch("builtins.print"),
        ):
            result = cli.main(["--loop", "--migrate"])

        self.assertEqual(result, 0)
        worker_cls.assert_called_once()
        self.assertTrue(worker_cls.call_args.kwargs["migrate"])

    def test_loop_keyboard_interrupt_during_sleep_stops_cleanly(self):
        config = WorkerConfig(pools=(make_pool(),), dry_run=True, interval_seconds=1800)
        worker = Mock()
        worker.run_once.return_value = [{"cycle": 1}]

        with patch("builtins.print") as print_mock:
            result = cli.run_loop(
                worker,
                config,
                sleep_fn=Mock(side_effect=KeyboardInterrupt()),
                time_fn=Mock(side_effect=[100.0, 101.0, 101.0]),
            )

        self.assertEqual(result, 0)
        worker.run_once.assert_called_once()
        self.assertEqual(print_mock.call_count, 1)

    def test_loop_cycle_error_is_printed_and_next_cycle_runs(self):
        config = WorkerConfig(pools=(make_pool(),), dry_run=True, interval_seconds=0)
        worker = Mock()
        worker.run_once.side_effect = [RuntimeError("boom"), [{"state": "RECOVERED"}], KeyboardInterrupt()]

        with patch("builtins.print") as print_mock:
            result = cli.run_loop(worker, config)

        self.assertEqual(result, 0)
        self.assertEqual(worker.run_once.call_count, 3)
        cycle_payloads = [
            json.loads(call.args[0])
            for call in print_mock.call_args_list
            if call.args and str(call.args[0]).lstrip().startswith("{")
        ]
        self.assertEqual(len(cycle_payloads), 2)
        error_payload = cycle_payloads[0]
        self.assertEqual(error_payload["status"], "ERROR")
        self.assertEqual(error_payload["error"], "boom")
        success_payload = cycle_payloads[1]
        self.assertEqual(success_payload["status"], "SUCCESS")


if __name__ == "__main__":
    unittest.main()
