from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from .paths import ENV_FILE, LOCAL_CONFIG
from .client import BinanceFuturesClient
from .config import load_monitor_config
from .credentials import load_partial_credentials, prompt_runtime_credentials
from .models import BinanceCredentials
from .repository import BinanceMonitorRepository
from .worker import BinanceFuturesMonitor


log = logging.getLogger("binance_futures_monitor")
DEFAULT_CREDENTIALS_ENV = ENV_FILE


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Interactive Binance Futures position monitor")
    parser.add_argument(
        "--config",
        default=str(LOCAL_CONFIG),
        help="Path to local JSON config",
    )
    parser.add_argument(
        "--credentials-env",
        default=str(DEFAULT_CREDENTIALS_ENV),
        help="Partial Binance credentials file (default: project-root .env; no fallback)",
    )
    parser.add_argument("--migrate", action="store_true", help="Create monitor tables")
    parser.add_argument("--loop", action="store_true", help="Run continuously")
    parser.add_argument("--log-level", default="INFO")
    return parser.parse_args(argv)


def run_loop(worker: BinanceFuturesMonitor, interval_seconds: int) -> int:
    while True:
        started_at = time.monotonic()
        try:
            print(json.dumps(worker.run_once(), indent=2, sort_keys=True), flush=True)
        except KeyboardInterrupt:
            log.info("monitor stopped by operator")
            return 0
        except Exception:
            log.exception("monitor cycle failed")
        remaining = interval_seconds - (time.monotonic() - started_at)
        try:
            time.sleep(max(0, remaining))
        except KeyboardInterrupt:
            log.info("monitor stopped by operator")
            return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )
    config = load_monitor_config(args.config)
    partial_credentials = load_partial_credentials(
        args.credentials_env, config.accounts
    )
    client = BinanceFuturesClient()
    market_by_account = {
        account.alias: account.markets[0]
        for account in config.accounts
    }

    def validate_credentials(
        account_alias: str,
        credentials: BinanceCredentials,
    ) -> None:
        market = market_by_account[account_alias]
        client.validate_credentials(market, credentials)
        log.info(
            "Binance credentials verified account=%s market=%s",
            account_alias,
            market.value,
        )

    repository = BinanceMonitorRepository()
    if args.migrate:
        repository.migrate()
        
    collisions = repository.check_alias_collisions(config)
    if collisions:
        error_msg = (
            f"NGĂN CHẶN GHI ĐÈ: Các account_alias {', '.join(collisions)} đã tồn tại "
            "trong database của người khác (danh sách ví khác biệt hoàn toàn). "
            "Để bảo vệ dữ liệu, tool đã dừng hoạt động. "
            "Vui lòng đổi tên alias trong file config.json thành tên khác (ví dụ: them tien_to_ten_ban)!"
        )
        log.error(error_msg)
        sys.exit(1)

    credentials = prompt_runtime_credentials(
        partial_credentials,
        validator=validate_credentials,
    )
    worker = BinanceFuturesMonitor(
        config,
        credentials,
        client=client,
        repository=repository,
    )
    if args.loop:
        return run_loop(worker, config.interval_seconds)
    print(json.dumps(worker.run_once(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
