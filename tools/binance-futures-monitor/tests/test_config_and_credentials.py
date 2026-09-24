from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from binance_futures_monitor import cli
from binance_futures_monitor.client import BinanceMonitorError
from binance_futures_monitor.config import load_monitor_config
from binance_futures_monitor.credentials import (
    credential_env_alias,
    load_partial_credentials,
    prompt_runtime_credentials,
)
from binance_futures_monitor.models import (
    BinanceCredentials,
    PartialBinanceCredentials,
    RuntimeBinanceCredentials,
)


WALLET_A = "0x0000000000000000000000000000000000000001"
WALLET_B = "0x0000000000000000000000000000000000000002"


def write_config(payload) -> Path:
    handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8")
    with handle:
        json.dump(payload, handle)
    return Path(handle.name)


def write_env(content: str) -> Path:
    handle = tempfile.NamedTemporaryFile(
        "w", suffix=".env", delete=False, encoding="utf-8"
    )
    with handle:
        handle.write(content)
    return Path(handle.name)


class ConfigAndCredentialsTests(unittest.TestCase):
    def test_account_supports_both_markets_and_multiple_wallets(self):
        path = write_config({
            "version": 1,
            "accounts": [{
                "alias": "main",
                "markets": ["USD_M", "COIN_M"],
                "linked_wallets": [WALLET_A, WALLET_B],
            }],
        })
        self.addCleanup(path.unlink)

        config = load_monitor_config(path)

        self.assertEqual([market.value for market in config.accounts[0].markets], ["USD_M", "COIN_M"])
        self.assertEqual(len(config.accounts[0].linked_wallets), 2)
        self.assertEqual(config.accounts[0].linked_wallets[0].wallet_address, WALLET_A.lower())

    def test_duplicate_alias_is_rejected(self):
        account = {
            "alias": "main",
            "markets": ["USD_M"],
            "linked_wallets": [WALLET_A],
        }
        path = write_config({"accounts": [account, account]})
        self.addCleanup(path.unlink)

        with self.assertRaisesRegex(ValueError, "duplicate account alias"):
            load_monitor_config(path)

    def test_credentials_in_config_are_rejected(self):
        for forbidden_field in (
            "api_key",
            "secret_key",
            "secret_prefix",
            "secret_key_prefix",
        ):
            with self.subTest(forbidden_field=forbidden_field):
                path = write_config({
                    "version": 1,
                    "accounts": [{
                        "alias": "main",
                        "markets": ["USD_M"],
                        "linked_wallets": [WALLET_A],
                        forbidden_field: "must-not-be-here",
                    }],
                })
                self.addCleanup(path.unlink)

                with self.assertRaisesRegex(ValueError, "credentials are not allowed"):
                    load_monitor_config(path)

    def test_partial_credentials_load_from_file_without_changing_environment(self):
        config_path = write_config({
            "accounts": [{
                "alias": "main-hedge",
                "markets": ["USD_M", "COIN_M"],
                "linked_wallets": [WALLET_A],
            }],
        })
        env_path = write_env(
            "BINANCE_MAIN_HEDGE_API_KEY=file-api-key\n"
            "BINANCE_MAIN_HEDGE_SECRET_PREFIX=file-secret-prefix\n"
        )
        self.addCleanup(config_path.unlink)
        self.addCleanup(env_path.unlink)
        config = load_monitor_config(config_path)
        previous_api_key = os.environ.get("BINANCE_MAIN_HEDGE_API_KEY")

        partial = load_partial_credentials(env_path, config.accounts)

        self.assertEqual(partial["main-hedge"].api_key, "file-api-key")
        self.assertEqual(
            partial["main-hedge"].secret_key_prefix, "file-secret-prefix"
        )
        self.assertEqual(
            os.environ.get("BINANCE_MAIN_HEDGE_API_KEY"), previous_api_key
        )

    def test_empty_secret_prefix_is_allowed_for_full_terminal_input(self):
        config_path = write_config({
            "accounts": [{
                "alias": "main",
                "markets": ["USD_M"],
                "linked_wallets": [WALLET_A],
            }],
        })
        env_path = write_env(
            "BINANCE_MAIN_API_KEY=file-api-key\n"
            "BINANCE_MAIN_SECRET_PREFIX=\n"
        )
        self.addCleanup(config_path.unlink)
        self.addCleanup(env_path.unlink)
        config = load_monitor_config(config_path)

        partial = load_partial_credentials(env_path, config.accounts)

        self.assertEqual(partial["main"].secret_key_prefix, "")
        runtime = prompt_runtime_credentials(
            partial,
            validator=lambda _alias, _credentials: None,
            prompt=lambda _message: "full-terminal-secret",
        )
        self.assertEqual(
            runtime.for_account("main").secret_key,
            "full-terminal-secret",
        )

    def test_credential_alias_is_normalized(self):
        self.assertEqual(credential_env_alias("main-hedge"), "MAIN_HEDGE")
        self.assertEqual(credential_env_alias(" main.hedge "), "MAIN_HEDGE")

    def test_normalized_alias_collision_is_rejected(self):
        config_path = write_config({
            "accounts": [
                {
                    "alias": "main-hedge",
                    "markets": ["USD_M"],
                    "linked_wallets": [WALLET_A],
                },
                {
                    "alias": "main_hedge",
                    "markets": ["COIN_M"],
                    "linked_wallets": [WALLET_B],
                },
            ],
        })
        env_path = write_env(
            "BINANCE_MAIN_HEDGE_API_KEY=file-api-key\n"
            "BINANCE_MAIN_HEDGE_SECRET_PREFIX=file-secret-prefix\n"
        )
        self.addCleanup(config_path.unlink)
        self.addCleanup(env_path.unlink)
        config = load_monitor_config(config_path)

        with self.assertRaisesRegex(ValueError, "alias collision"):
            load_partial_credentials(env_path, config.accounts)

    def test_missing_credentials_file_is_rejected(self):
        config_path = write_config({
            "accounts": [{
                "alias": "main",
                "markets": ["USD_M"],
                "linked_wallets": [WALLET_A],
            }],
        })
        self.addCleanup(config_path.unlink)
        config = load_monitor_config(config_path)

        with self.assertRaisesRegex(FileNotFoundError, "credentials env file"):
            load_partial_credentials("missing-binance-credentials.env", config.accounts)

    def test_missing_env_value_does_not_expose_existing_value(self):
        config_path = write_config({
            "accounts": [{
                "alias": "main",
                "markets": ["USD_M"],
                "linked_wallets": [WALLET_A],
            }],
        })
        env_path = write_env("BINANCE_MAIN_API_KEY=sensitive-api-key\n")
        self.addCleanup(config_path.unlink)
        self.addCleanup(env_path.unlink)
        config = load_monitor_config(config_path)

        with self.assertRaises(ValueError) as raised:
            load_partial_credentials(env_path, config.accounts)

        self.assertIn("BINANCE_MAIN_SECRET_PREFIX", str(raised.exception))
        self.assertNotIn("sensitive-api-key", str(raised.exception))

    def test_process_environment_is_not_a_credential_fallback(self):
        config_path = write_config({
            "accounts": [{
                "alias": "main",
                "markets": ["USD_M"],
                "linked_wallets": [WALLET_A],
            }],
        })
        env_path = write_env("")
        self.addCleanup(config_path.unlink)
        self.addCleanup(env_path.unlink)
        config = load_monitor_config(config_path)

        with patch.dict(os.environ, {
            "BINANCE_MAIN_API_KEY": "process-api-key",
            "BINANCE_MAIN_SECRET_PREFIX": "process-secret-prefix",
        }):
            with self.assertRaisesRegex(ValueError, "BINANCE_MAIN_API_KEY"):
                load_partial_credentials(env_path, config.accounts)

    def test_cli_default_credentials_path_is_project_root_from_other_directory(self):
        expected = Path(__file__).resolve().parents[3] / ".env"
        previous_directory = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                args = cli.parse_args([])
                self.assertEqual(Path(args.credentials_env), expected)
            finally:
                os.chdir(previous_directory)

    def test_cli_credentials_override_uses_only_selected_file(self):
        config_path = write_config({
            "accounts": [{
                "alias": "main",
                "markets": ["USD_M"],
                "linked_wallets": [WALLET_A],
            }],
        })
        env_path = write_env(
            "UNRELATED_SETTING=ignored\n"
            "BINANCE_MAIN_API_KEY=selected-api-key\n"
            "BINANCE_MAIN_SECRET_PREFIX=${LITERAL_PREFIX}\n"
        )
        self.addCleanup(config_path.unlink)
        self.addCleanup(env_path.unlink)
        config = load_monitor_config(config_path)

        with patch.dict(os.environ, {
            "BINANCE_MAIN_API_KEY": "environment-api-key",
            "LITERAL_PREFIX": "must-not-expand",
        }):
            args = cli.parse_args(["--credentials-env", str(env_path)])
            partial = load_partial_credentials(args.credentials_env, config.accounts)

        self.assertEqual(partial["main"].api_key, "selected-api-key")
        self.assertEqual(partial["main"].secret_key_prefix, "${LITERAL_PREFIX}")

    def test_runtime_credentials_repr_never_contains_secrets(self):
        runtime = RuntimeBinanceCredentials({
            "main": BinanceCredentials("visible-api-key", "very-secret-key")
        })

        value = repr(runtime)

        self.assertIn("main", value)
        self.assertNotIn("visible-api-key", value)
        self.assertNotIn("very-secret-key", value)

        partial_value = repr(
            PartialBinanceCredentials("partial-api-key", "secret-prefix")
        )
        self.assertNotIn("partial-api-key", partial_value)
        self.assertNotIn("secret-prefix", partial_value)

    def test_prompts_one_arbitrary_length_suffix_and_validates_full_secret(self):
        config_path = write_config({
            "accounts": [{
                "alias": "main-hedge",
                "markets": ["USD_M", "COIN_M"],
                "linked_wallets": [WALLET_A],
            }],
        })
        env_path = write_env(
            "BINANCE_MAIN_HEDGE_API_KEY=file-api-key\n"
            "BINANCE_MAIN_HEDGE_SECRET_PREFIX=secret-prefix-\n"
        )
        self.addCleanup(config_path.unlink)
        self.addCleanup(env_path.unlink)
        config = load_monitor_config(config_path)
        partial = load_partial_credentials(env_path, config.accounts)
        prompts = []

        validated = []

        def prompt(message):
            prompts.append(message)
            return "remaining-secret"

        def validator(account_alias, credentials):
            validated.append((account_alias, credentials.secret_key))

        runtime = prompt_runtime_credentials(
            partial,
            validator=validator,
            prompt=prompt,
        )

        credentials = runtime.for_account("main-hedge")
        self.assertEqual(len(prompts), 1)
        self.assertIn("attempt 1/3", prompts[0])
        self.assertEqual(credentials.api_key, "file-api-key")
        self.assertEqual(credentials.secret_key, "secret-prefix-remaining-secret")
        self.assertEqual(validated, [("main-hedge", credentials.secret_key)])

    def test_empty_secret_suffix_is_prompted_again(self):
        partial = {
            "main": PartialBinanceCredentials("api-key", "secret-prefix")
        }
        prompts = []

        runtime = prompt_runtime_credentials(
            partial,
            validator=lambda _alias, _credentials: None,
            prompt=lambda message: prompts.append(message) or ("" if len(prompts) == 1 else "suffix"),
        )

        self.assertEqual(len(prompts), 2)
        self.assertEqual(runtime.for_account("main").secret_key, "secret-prefixsuffix")

    def test_invalid_signature_is_reprompted_and_only_valid_secret_is_kept(self):
        partial = {
            "main": PartialBinanceCredentials("api-key", "prefix-")
        }
        validated = []

        def validator(_account_alias, credentials):
            validated.append(credentials.secret_key)
            if len(validated) == 1:
                raise BinanceMonitorError("INVALID_SIGNATURE", "sanitized", -1022)

        runtime = prompt_runtime_credentials(
            partial,
            validator=validator,
            prompt=lambda _message: "wrong" if not validated else "correct",
        )

        self.assertEqual(validated, ["prefix-wrong", "prefix-correct"])
        self.assertEqual(runtime.for_account("main").secret_key, "prefix-correct")

    def test_multiple_accounts_are_prompted_and_validated_independently(self):
        partial = {
            "main": PartialBinanceCredentials("main-api-key", "main-prefix-"),
            "backup": PartialBinanceCredentials("backup-api-key", "backup-prefix-"),
        }
        suffixes = iter(("main-suffix", "backup-suffix"))
        validated = []

        runtime = prompt_runtime_credentials(
            partial,
            validator=lambda alias, credentials: validated.append(
                (alias, credentials.secret_key)
            ),
            prompt=lambda _message: next(suffixes),
        )

        self.assertEqual(
            validated,
            [
                ("main", "main-prefix-main-suffix"),
                ("backup", "backup-prefix-backup-suffix"),
            ],
        )
        self.assertEqual(
            runtime.for_account("backup").secret_key,
            "backup-prefix-backup-suffix",
        )

    def test_three_invalid_signatures_abort_without_leaking_secret(self):
        partial = {
            "main": PartialBinanceCredentials("api-key", "sensitive-prefix-")
        }
        suffix = "sensitive-suffix"

        def reject_signature(_account_alias, _credentials):
            raise BinanceMonitorError("INVALID_SIGNATURE", "sanitized", -1022)

        with self.assertLogs("binance_futures_monitor", level="WARNING") as logs:
            with self.assertRaisesRegex(ValueError, "after 3 attempts") as raised:
                prompt_runtime_credentials(
                    partial,
                    validator=reject_signature,
                    prompt=lambda _message: suffix,
                )

        output = "\n".join(logs.output) + str(raised.exception)
        self.assertNotIn("sensitive-prefix-", output)
        self.assertNotIn(suffix, output)
        self.assertIn("attempts=3", output)

    def test_non_signature_validation_error_does_not_reprompt(self):
        partial = {
            "main": PartialBinanceCredentials("api-key", "prefix-")
        }
        prompts = []

        def reject_auth(_account_alias, _credentials):
            raise BinanceMonitorError("AUTH_OR_IP", "sanitized", -2015)

        with self.assertRaises(BinanceMonitorError) as raised:
            prompt_runtime_credentials(
                partial,
                validator=reject_auth,
                prompt=lambda message: prompts.append(message) or "suffix",
            )

        self.assertEqual(raised.exception.category, "AUTH_OR_IP")
        self.assertEqual(len(prompts), 1)

    def test_cli_validates_credentials_before_starting_worker(self):
        config_path = write_config({
            "accounts": [{
                "alias": "main",
                "markets": ["USD_M", "COIN_M"],
                "linked_wallets": [WALLET_A],
            }],
        })
        env_path = write_env(
            "BINANCE_MAIN_API_KEY=file-api-key\n"
            "BINANCE_MAIN_SECRET_PREFIX=prefix-\n"
        )
        self.addCleanup(config_path.unlink)
        self.addCleanup(env_path.unlink)
        client = Mock()
        repository = Mock()
        repository.check_alias_collisions.return_value = []
        worker = Mock()
        worker.run_once.return_value = []

        with (
            patch(
                "binance_futures_monitor.cli.BinanceFuturesClient",
                return_value=client,
            ),
            patch(
                "binance_futures_monitor.cli.BinanceMonitorRepository",
                return_value=repository,
            ),
            patch(
                "binance_futures_monitor.cli.BinanceFuturesMonitor",
                return_value=worker,
            ) as worker_cls,
            patch(
                "binance_futures_monitor.credentials.getpass.getpass",
                return_value="suffix",
            ),
            patch("builtins.print"),
        ):
            result = cli.main([
                "--config",
                str(config_path),
                "--credentials-env",
                str(env_path),
            ])

        self.assertEqual(result, 0)
        client.validate_credentials.assert_called_once()
        market, credentials = client.validate_credentials.call_args.args
        self.assertEqual(market.value, "USD_M")
        self.assertEqual(credentials.secret_key, "prefix-suffix")
        worker_cls.assert_called_once()
        self.assertIs(worker_cls.call_args.kwargs["client"], client)
        worker.run_once.assert_called_once()


if __name__ == "__main__":
    unittest.main()
