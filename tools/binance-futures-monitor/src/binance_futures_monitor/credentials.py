from __future__ import annotations

import getpass
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from dotenv import dotenv_values

from .client import BinanceMonitorError
from .models import (
    AccountConfig,
    BinanceCredentials,
    PartialBinanceCredentials,
    RuntimeBinanceCredentials,
)


MAX_SECRET_INPUT_ATTEMPTS = 3
log = logging.getLogger("binance_futures_monitor")


def credential_env_alias(account_alias: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", account_alias.upper()).strip("_")


def load_partial_credentials(
    path: str | Path,
    accounts: Sequence[AccountConfig],
) -> dict[str, PartialBinanceCredentials]:
    credentials_path = Path(path)
    if not credentials_path.is_file():
        raise FileNotFoundError(
            f"Binance credentials env file not found: {credentials_path}"
        )

    values = dotenv_values(credentials_path, interpolate=False)
    aliases_by_env: dict[str, str] = {}
    credentials: dict[str, PartialBinanceCredentials] = {}

    for account in accounts:
        env_alias = credential_env_alias(account.alias)
        if not env_alias:
            raise ValueError(
                f"account alias {account.alias!r} cannot form credential variable names"
            )
        existing_alias = aliases_by_env.get(env_alias)
        if existing_alias is not None:
            raise ValueError(
                "credential environment alias collision between "
                f"{existing_alias!r} and {account.alias!r}"
            )
        aliases_by_env[env_alias] = account.alias

        api_key_name = f"BINANCE_{env_alias}_API_KEY"
        secret_prefix_name = f"BINANCE_{env_alias}_SECRET_PREFIX"
        api_key = values.get(api_key_name)
        secret_key_prefix = values.get(secret_prefix_name)
        if not isinstance(api_key, str) or not api_key:
            raise ValueError(
                f"missing {api_key_name} for Binance account {account.alias!r}"
            )
        if secret_prefix_name not in values or not isinstance(secret_key_prefix, str):
            raise ValueError(
                f"missing {secret_prefix_name} for Binance account {account.alias!r}"
            )
        credentials[account.alias] = PartialBinanceCredentials(
            api_key=api_key,
            secret_key_prefix=secret_key_prefix,
        )

    return credentials


def prompt_runtime_credentials(
    partial_credentials: Mapping[str, PartialBinanceCredentials],
    validator: Callable[[str, BinanceCredentials], None],
    prompt: Callable[[str], str] | None = None,
) -> RuntimeBinanceCredentials:
    prompt_fn = prompt or getpass.getpass
    credentials: dict[str, BinanceCredentials] = {}
    for account_alias, partial in partial_credentials.items():
        for attempt in range(1, MAX_SECRET_INPUT_ATTEMPTS + 1):
            suffix = prompt_fn(
                f"Enter Binance secret suffix for account {account_alias} "
                f"(attempt {attempt}/{MAX_SECRET_INPUT_ATTEMPTS}): "
            )
            if not suffix:
                log.warning(
                    "Binance secret rejected account=%s attempt=%s/%s "
                    "reason=entered suffix is empty",
                    account_alias,
                    attempt,
                    MAX_SECRET_INPUT_ATTEMPTS,
                )
                continue

            candidate = BinanceCredentials(
                api_key=partial.api_key,
                secret_key=partial.secret_key_prefix + suffix,
            )
            try:
                validator(account_alias, candidate)
            except BinanceMonitorError as exc:
                if exc.category != "INVALID_SIGNATURE":
                    log.error(
                        "Binance credential validation failed account=%s reason=%s",
                        account_alias,
                        exc.category,
                    )
                    raise
                log.warning(
                    "Binance secret rejected account=%s attempt=%s/%s "
                    "reason=invalid signature; verify configured prefix and entered suffix",
                    account_alias,
                    attempt,
                    MAX_SECRET_INPUT_ATTEMPTS,
                )
                continue

            credentials[account_alias] = candidate
            break
        else:
            log.error(
                "Binance secret validation exhausted account=%s attempts=%s",
                account_alias,
                MAX_SECRET_INPUT_ATTEMPTS,
            )
            raise ValueError(
                f"Binance secret validation failed for account {account_alias!r} "
                f"after {MAX_SECRET_INPUT_ATTEMPTS} attempts"
            )
    return RuntimeBinanceCredentials(credentials)
