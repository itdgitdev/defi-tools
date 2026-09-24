from __future__ import annotations

import time
import logging
import os
import re
from urllib.parse import urlparse

from web3 import Web3

from .models import GasPolicy


from .chain_config import CHAIN_ID_MAP, CONFIGURED_REBALANCER_WRITE_RPC_URLS, RPC_BACKUP_LIST, RPC_URLS_2


log = logging.getLogger("configured_pool_rebalancer")

CHAIN_ALIASES = {
    "BSC": "BNB",
    "BASE": "BAS",
    "ETHEREUM": "ETH",
    "ARBITRUM": "ARB",
}

DELEGATED_INFLIGHT_RETRY_DELAYS = (2.0, 4.0, 8.0)
DELEGATED_INFLIGHT_ERROR = "in-flight transaction limit reached for delegated accounts"


def web3_connection(chain: str, timeout: int = 30) -> Web3:
    chain_key = normalize_chain(chain)
    urls = [RPC_URLS_2.get(chain_key)] + RPC_BACKUP_LIST.get(chain_key, [])
    for url in [item for item in urls if item]:
        rpc_label = _rpc_label(url)
        try:
            w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": timeout}))
            _inject_poa_middleware(w3, chain_key)
            if w3.is_connected():
                log.info("rpc selected chain=%s rpc=%s", chain_key, rpc_label)
                return w3
            log.warning("rpc connection unavailable chain=%s rpc=%s", chain_key, rpc_label)
        except Exception as exc:
            log.warning("rpc connection failed chain=%s rpc=%s error=%s", chain_key, rpc_label, exc)
            time.sleep(0.5)
    raise RuntimeError(f"No working RPC for chain={chain_key}")


def protected_write_connection(chain: str, timeout: int = 30) -> Web3:
    chain_key = normalize_chain(chain)
    env_name = f"CONFIGURED_REBALANCER_WRITE_RPC_{chain_key}"
    url = os.getenv(env_name) or CONFIGURED_REBALANCER_WRITE_RPC_URLS.get(chain_key)
    if not url:
        raise RuntimeError(f"Protected write RPC is required for chain={chain_key}")

    rpc_label = _rpc_label(url)
    try:
        w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": timeout}))
        _inject_poa_middleware(w3, chain_key)
        actual_chain_id = int(w3.eth.chain_id)
    except Exception as exc:
        log.error(
            "protected write rpc unavailable chain=%s rpc=%s error_type=%s",
            chain_key,
            rpc_label,
            type(exc).__name__,
        )
        raise RuntimeError(f"Protected write RPC unavailable for chain={chain_key}") from None

    expected_chain_id = get_chain_id(chain_key)
    if actual_chain_id != expected_chain_id:
        raise RuntimeError(
            f"Protected write RPC chain mismatch for chain={chain_key}: "
            f"expected={expected_chain_id} actual={actual_chain_id}"
        )
    log.info("protected write rpc selected chain=%s rpc=%s", chain_key, rpc_label)
    return w3


def normalize_chain(chain: str) -> str:
    chain_key = str(chain or "").strip().upper()
    return CHAIN_ALIASES.get(chain_key, chain_key)


def _inject_poa_middleware(w3: Web3, chain: str) -> None:
    if chain != "BNB":
        return
    try:
        from web3.middleware import ExtraDataToPOAMiddleware

        w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    except ImportError:
        from web3.middleware import geth_poa_middleware

        w3.middleware_onion.inject(geth_poa_middleware, layer=0)


def _rpc_label(url: str) -> str:
    try:
        parsed = urlparse(url)
        return parsed.netloc or "unknown-rpc"
    except Exception:
        return "unknown-rpc"


def safe_rpc_error(exc: Exception) -> str:
    message = re.sub(r"https?://[^\s\"']+", "<redacted-rpc-url>", str(exc))
    return f"{type(exc).__name__}: {message}"[:1000]


def is_delegated_inflight_error(exc: Exception) -> bool:
    return DELEGATED_INFLIGHT_ERROR in str(exc).lower()


def send_protected_raw_transaction(
    write_w3: Web3,
    raw_transaction: bytes,
    chain: str,
):
    signed_tx_hash = Web3.to_hex(Web3.keccak(raw_transaction))
    for attempt in range(len(DELEGATED_INFLIGHT_RETRY_DELAYS) + 1):
        try:
            return write_w3.eth.send_raw_transaction(raw_transaction)
        except Exception as exc:
            if (
                not is_delegated_inflight_error(exc)
                or attempt >= len(DELEGATED_INFLIGHT_RETRY_DELAYS)
            ):
                raise
            delay = DELEGATED_INFLIGHT_RETRY_DELAYS[attempt]
            log.warning(
                "protected broadcast retry chain=%s attempt=%s next_attempt=%s "
                "delay_seconds=%s signed_tx_hash=%s error=%s",
                normalize_chain(chain),
                attempt + 1,
                attempt + 2,
                delay,
                signed_tx_hash,
                safe_rpc_error(exc),
            )
            time.sleep(delay)
    raise RuntimeError("protected broadcast retry loop exhausted")


def get_chain_id(chain: str) -> int:
    return int(CHAIN_ID_MAP.get(normalize_chain(chain), 56))


DEFAULT_GAS_POLICIES = {
    "BNB": GasPolicy(mode="fixed", gas_price_gwei=0.05, max_fee_gwei=0.08),
    "BAS": GasPolicy(
        mode="eip1559",
        base_fee_multiplier=2.0,
        priority_fee_cap_gwei=0.01,
        swap_priority_fee_cap_gwei=0.02,
        swap_priority_fee_floor_gwei=0.005,
        max_fee_gwei=0.10,
    ),
}


def get_gas_params(
    w3: Web3,
    chain: str,
    action: str = "default",
    policy: GasPolicy | None = None,
) -> dict:
    chain_key = chain.upper()
    gas_policy = policy or DEFAULT_GAS_POLICIES.get(chain_key) or GasPolicy()
    mode = gas_policy.mode.lower()
    if mode == "fixed":
        gas_price_gwei = gas_policy.gas_price_gwei
        if gas_price_gwei is None:
            gas_price_gwei = 0.05 if chain_key == "BNB" else float(Web3.from_wei(w3.eth.gas_price, "gwei"))
        gas_price = Web3.to_wei(gas_price_gwei, "gwei")
        return {"maxFeePerGas": gas_price, "maxPriorityFeePerGas": gas_price}

    priority_tip = _priority_fee(w3, gas_policy, action)
    try:
        fee_history = w3.eth.fee_history(1, "latest", [50])
        base_fee = fee_history["baseFeePerGas"][-1]
    except Exception:
        base_fee = w3.eth.gas_price
    max_fee = int(base_fee * gas_policy.base_fee_multiplier + priority_tip)
    if max_fee < priority_tip:
        max_fee = priority_tip + int(base_fee)
    return {"maxFeePerGas": max_fee, "maxPriorityFeePerGas": priority_tip}


def validate_gas_cap(gas_params: dict, max_fee_gwei: float | None) -> float:
    max_fee = float(Web3.from_wei(gas_params["maxFeePerGas"], "gwei"))
    if max_fee_gwei is not None and max_fee > max_fee_gwei:
        raise RuntimeError(f"gas too high: {max_fee:.6f} gwei > cap {max_fee_gwei:.6f} gwei")
    return max_fee


def _priority_fee(w3: Web3, policy: GasPolicy, action: str) -> int:
    cap_gwei = policy.swap_priority_fee_cap_gwei if action == "swap" else policy.priority_fee_cap_gwei
    suggested = None
    try:
        suggested = int(w3.eth.max_priority_fee)
    except Exception:
        pass
    if suggested is None:
        try:
            fee_history = w3.eth.fee_history(3, "latest", [50])
            rewards = [int(row[0]) for row in fee_history.get("reward", []) if row]
            if rewards:
                suggested = max(rewards)
        except Exception:
            pass
    if suggested is None:
        suggested = Web3.to_wei(0.05, "gwei")
    if action == "swap" and policy.swap_priority_fee_floor_gwei is not None:
        suggested = max(suggested, Web3.to_wei(policy.swap_priority_fee_floor_gwei, "gwei"))
    if cap_gwei is not None:
        suggested = min(suggested, Web3.to_wei(cap_gwei, "gwei"))
    return max(0, int(suggested))
