from __future__ import annotations

import argparse
import getpass
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from typing import Callable

from dotenv import load_dotenv
from web3 import Web3

from .paths import ENV_FILE, LOCAL_CONFIG, LOG_DIR
from .models import WorkerConfig
from .settings import load_worker_config
from .signer import RuntimeSigner
from .worker import ConfiguredPoolRebalancer
from .pnl_report import ConfiguredPoolPnlReporter
from .automation_worker import ConfiguredPoolAutomationWorker

log = logging.getLogger("configured_pool_rebalancer")

PRIVATE_KEY_HEX_LENGTH = 64
MAX_KEY_INPUT_ATTEMPTS = 3
HEX_PATTERN = re.compile(r"^[0-9a-fA-F]+$")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Configured multi-pool V3 LP rebalancer")
    parser.add_argument(
        "--config",
        default=os.getenv("CONFIGURED_REBALANCER_CONFIG", str(LOCAL_CONFIG)),
        help="Path to JSON config file",
    )
    parser.add_argument("--execute", action="store_true", help="Send transactions. Default is dry-run.")
    parser.add_argument("--loop", action="store_true", help="Run continuously every interval_seconds.")
    parser.add_argument("--migrate", action="store_true", help="Create journal tables before running.")
    parser.add_argument("--pnl-report", action="store_true", help="Generate PnL report only. Does not rebalance or migrate.")
    parser.add_argument("--pnl-output-dir", default=str(LOG_DIR), help="Directory for PnL report files.")
    parser.add_argument("--pnl-format", choices=("json", "csv", "both"), default="both", help="PnL report output format.")
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args(argv)


def configure_logging(log_level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
    for logger_name in ("mysql", "mysql.connector", "mysql.connector.plugins"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)


def prompt_runtime_signer(config: WorkerConfig) -> RuntimeSigner | None:
    if config.dry_run:
        return None

    prefix_env_by_wallet: dict[str, str] = {}
    for pool in config.pools:
        wallet = Web3.to_checksum_address(pool.bot_wallet)
        env_name = str(pool.private_key_prefix_env or "").strip()
        if not env_name:
            raise ValueError(f"private_key_prefix_env is required for bot_wallet {wallet}")
        existing_env_name = prefix_env_by_wallet.get(wallet)
        if existing_env_name and existing_env_name != env_name:
            raise ValueError(
                f"conflicting private_key_prefix_env values for bot_wallet {wallet}: "
                f"{existing_env_name} and {env_name}"
            )
        prefix_env_by_wallet[wallet] = env_name

    prefix_by_wallet: dict[str, str] = {}
    for wallet, env_name in prefix_env_by_wallet.items():
        if env_name not in os.environ:
            raise ValueError(
                f"missing private key prefix in environment variable {env_name} "
                f"for bot_wallet {wallet}"
            )
        prefix = os.environ[env_name].strip()
        if len(prefix) >= PRIVATE_KEY_HEX_LENGTH:
            raise ValueError(
                f"private key prefix from environment variable {env_name} for "
                f"bot_wallet {wallet} must contain between 0 and 63 hexadecimal characters"
            )
        if prefix and not HEX_PATTERN.fullmatch(prefix):
            raise ValueError(
                f"private key prefix from environment variable {env_name} for "
                f"bot_wallet {wallet} must contain only hexadecimal characters"
            )
        prefix_by_wallet[wallet] = prefix

    private_keys_by_wallet: dict[str, str] = {}
    for wallet, prefix in prefix_by_wallet.items():
        expected_suffix_length = PRIVATE_KEY_HEX_LENGTH - len(prefix)
        for attempt in range(1, MAX_KEY_INPUT_ATTEMPTS + 1):
            suffix = getpass.getpass(
                f"Enter the remaining {expected_suffix_length} private-key hex characters "
                f"for bot_wallet {wallet} (attempt {attempt}/{MAX_KEY_INPUT_ATTEMPTS}): "
            ).strip()
            if len(suffix) != expected_suffix_length:
                log.warning(
                    "Private-key suffix rejected wallet=%s attempt=%s/%s "
                    "reason=expected %s hexadecimal characters, received %s",
                    wallet,
                    attempt,
                    MAX_KEY_INPUT_ATTEMPTS,
                    expected_suffix_length,
                    len(suffix),
                )
                continue
            if not HEX_PATTERN.fullmatch(suffix):
                log.warning(
                    "Private-key suffix rejected wallet=%s attempt=%s/%s "
                    "reason=entered suffix is not hexadecimal",
                    wallet,
                    attempt,
                    MAX_KEY_INPUT_ATTEMPTS,
                )
                continue

            private_key = f"0x{prefix}{suffix}"
            try:
                RuntimeSigner({wallet: private_key})
            except ValueError as exc:
                reason = (
                    "reconstructed key does not match bot_wallet"
                    if "does not match bot_wallet" in str(exc)
                    else "reconstructed private key is invalid"
                )
                log.warning(
                    "Private-key verification failed wallet=%s attempt=%s/%s reason=%s",
                    wallet,
                    attempt,
                    MAX_KEY_INPUT_ATTEMPTS,
                    reason,
                )
                continue

            private_keys_by_wallet[wallet] = private_key
            log.info("Private-key signer verified wallet=%s", wallet)
            break
        else:
            log.error(
                "Private-key validation exhausted wallet=%s attempts=%s",
                wallet,
                MAX_KEY_INPUT_ATTEMPTS,
            )
            raise ValueError(
                f"private-key validation failed for bot_wallet {wallet} "
                f"after {MAX_KEY_INPUT_ATTEMPTS} attempts"
            )
    return RuntimeSigner(private_keys_by_wallet)


def run_loop(
    worker: ConfiguredPoolRebalancer,
    config: WorkerConfig,
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
    time_fn: Callable[[], float] = time.time,
) -> int:
    cycle = 1
    interval_seconds = max(0, int(config.interval_seconds))
    while True:
        started_ts = time_fn()
        try:
            records = worker.run_once()
            status = "SUCCESS"
            error = None
        except KeyboardInterrupt:
            log.info("loop stopped by operator during cycle")
            return 0
        except Exception as exc:
            log.exception("loop cycle failed: %s", exc)
            records = []
            status = "ERROR"
            error = str(exc)

        finished_ts = time_fn()
        next_run_ts = started_ts + interval_seconds
        payload = {
            "cycle": cycle,
            "status": status,
            "started_at": _iso_utc(started_ts),
            "finished_at": _iso_utc(finished_ts),
            "next_run_at": _iso_utc(next_run_ts),
            "interval_seconds": interval_seconds,
            "records": records,
        }
        if error:
            payload["error"] = error
        log.info(
            "loop cycle finished cycle=%s status=%s next_run_at=%s interval_seconds=%s",
            cycle,
            status,
            payload["next_run_at"],
            interval_seconds,
        )
        print(json.dumps(payload, indent=2, sort_keys=True), flush=True)

        try:
            _sleep_until(next_run_ts, sleep_fn=sleep_fn, time_fn=time_fn)
        except KeyboardInterrupt:
            log.info("loop stopped by operator during sleep")
            return 0
        cycle += 1


def _sleep_until(
    target_ts: float,
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
    time_fn: Callable[[], float] = time.time,
) -> None:
    while True:
        remaining = target_ts - time_fn()
        if remaining <= 0:
            return
        sleep_fn(min(1.0, remaining))


def _iso_utc(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


def main(argv: list[str] | None = None) -> int:
    load_dotenv(ENV_FILE, override=False)
    args = parse_args(argv)
    configure_logging(args.log_level)
    config = load_worker_config(args.config, dry_run=not args.execute)
    if args.pnl_report:
        result = ConfiguredPoolPnlReporter(config).write_report(args.pnl_output_dir, args.pnl_format)
        print(json.dumps({"records": result.records, "written_files": result.written_files}, indent=2, sort_keys=True))
        return 0

    signer = prompt_runtime_signer(config)
    compound_enabled = any(pool.auto_compound.enabled for pool in config.pools)
    if compound_enabled:
        worker = ConfiguredPoolAutomationWorker(config, migrate=args.migrate, signer=signer)
    else:
        worker = ConfiguredPoolRebalancer(config, migrate=args.migrate, signer=signer)
    if args.loop:
        return run_loop(worker, config)
    result = worker.run_once()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
