from __future__ import annotations

import logging
from itertools import count
from typing import Callable
from urllib.parse import urlparse

from web3 import Web3
from web3.exceptions import TimeExhausted, TransactionNotFound

from .evm import (
    DEFAULT_GAS_POLICIES,
    get_chain_id,
    get_gas_params,
    safe_rpc_error,
    send_protected_raw_transaction,
    validate_gas_cap,
)
from .logging_utils import log_block, pool_context
from .models import GasPolicy, PoolConfig, TxResult, WorkerConfig
from .signer import RuntimeSigner
from .tx_guard import WalletTxGuard

log = logging.getLogger("configured_pool_rebalancer")


class TxExecutor:
    def __init__(
        self,
        w3: Web3,
        pool: PoolConfig,
        dry_run: bool,
        worker_config: WorkerConfig | None = None,
        signer: RuntimeSigner | None = None,
        write_w3: Web3 | None = None,
        write_w3_factory: Callable[[], Web3] | None = None,
        tx_guard: WalletTxGuard | None = None,
    ):
        self.w3 = w3
        self.write_w3 = write_w3
        self.write_w3_factory = write_w3_factory
        self.pool = pool
        self.dry_run = dry_run
        self.worker_config = worker_config
        self.signer = signer
        self.tx_guard = tx_guard
        self._nonce_counter = None
        if not self.dry_run and self.signer is None:
            raise RuntimeError("runtime signer is required for live transactions")
        if not self.dry_run and self.tx_guard is None:
            raise RuntimeError("wallet transaction guard is required for live transactions")

    def require_write_w3(self) -> Web3:
        if self.write_w3 is not None:
            return self.write_w3
        if self.write_w3_factory is None:
            raise RuntimeError("protected write RPC is required for live transactions")
        write_w3 = self.write_w3_factory()
        if write_w3 is None:
            raise RuntimeError("protected write RPC is required for live transactions")
        self.write_w3 = write_w3
        return write_w3

    def _next_nonce(self) -> int:
        wallet = Web3.to_checksum_address(self.pool.bot_wallet)
        if self._nonce_counter is None:
            start = self.w3.eth.get_transaction_count(wallet, "pending")
            self._nonce_counter = count(start)
        return next(self._nonce_counter)

    def ensure_wallet_guard_clear(self) -> dict | None:
        if self.dry_run:
            return None
        wallet = Web3.to_checksum_address(self.pool.bot_wallet)
        return self.tx_guard.reconcile_receipt(
            self.pool.chain,
            wallet,
            self._read_attempts(),
        )

    def reserve_wallet_guard(
        self,
        action: str,
        nonce: int,
        signed_hash: str,
    ) -> None:
        self.tx_guard.reserve(
            self.pool.chain,
            self.pool.bot_wallet,
            nonce,
            signed_hash,
            action,
            self.pool.name,
        )

    def record_guard_broadcast(
        self,
        signed_hash: str,
        broadcast_hash: str,
    ) -> None:
        self.tx_guard.record_broadcast(
            self.pool.chain,
            self.pool.bot_wallet,
            signed_hash,
            broadcast_hash,
        )

    def record_guard_error(self, signed_hash: str, error: str) -> None:
        self.tx_guard.record_error(
            self.pool.chain,
            self.pool.bot_wallet,
            signed_hash,
            error,
        )

    def clear_wallet_guard(self, signed_hash: str) -> None:
        if not self.tx_guard.clear(
            self.pool.chain,
            self.pool.bot_wallet,
            signed_hash,
        ):
            raise RuntimeError(
                "wallet transaction guard changed before transaction completion"
            )

    def send(self, call_fn, label: str, gas: int | None = None, value: int = 0) -> TxResult:
        wallet = Web3.to_checksum_address(self.pool.bot_wallet)
        if self.dry_run:
            return TxResult(tx_hash=f"dry-run:{label}", dry_run=True, metadata={"label": label})

        gas_policy = self.gas_policy()
        gas_params = get_gas_params(self.w3, self.pool.chain, action=label, policy=gas_policy)
        cap = gas_policy.max_fee_gwei if gas_policy.max_fee_gwei is not None else self.pool.max_gas_gwei
        validate_gas_cap(gas_params, cap)

        if gas is None:
            try:
                estimated = call_fn.estimate_gas({"from": wallet, "value": value})
                gas = max(120000, int(estimated * 1.3))
            except Exception:
                gas = 900000

        self.ensure_wallet_guard_clear()
        write_w3 = self.require_write_w3()
        tx = call_fn.build_transaction(
            {
                "from": wallet,
                "nonce": self._next_nonce(),
                "gas": gas,
                "value": value,
                **gas_params,
                "chainId": get_chain_id(self.pool.chain),
            }
        )
        self._validate_protected_transaction(tx)
        metadata = self._safe_tx_metadata(label, tx, gas_params)
        signed = self.sign_transaction(wallet, tx)
        signed_tx_hash = Web3.keccak(signed.raw_transaction).hex()
        if not signed_tx_hash.startswith("0x"):
            signed_tx_hash = "0x" + signed_tx_hash
        metadata["signed_tx_hash"] = signed_tx_hash
        self.reserve_wallet_guard(label, int(tx["nonce"]), signed_tx_hash)
        log_block(
            log,
            logging.INFO,
            f"{label} broadcast",
            pool_context(self.pool),
            {
                "stage": "broadcast",
                "action": label,
                "to": metadata.get("to"),
                "value": metadata.get("value"),
                "nonce": metadata.get("nonce"),
                "gas_limit": metadata.get("gas_limit"),
                "max_fee_gwei": f"{metadata.get('max_fee_per_gas_gwei', 0.0):.9f}",
                "priority_fee_gwei": f"{metadata.get('max_priority_fee_per_gas_gwei', 0.0):.9f}",
                "data_length": metadata.get("data_length"),
                "signed_tx_hash": signed_tx_hash,
            },
        )
        tx_hash = None
        broadcast_errors = []
        receipt = None
        accepted_pending = False
        rpc_label = self.write_rpc_label()
        try:
            tx_hash = send_protected_raw_transaction(
                write_w3,
                signed.raw_transaction,
                self.pool.chain,
            )
            self.record_guard_broadcast(signed_tx_hash, self._hex_value(tx_hash))
            metadata["broadcast_rpc"] = rpc_label
        except Exception as exc:
            safe_error = safe_rpc_error(exc)
            self.record_guard_error(signed_tx_hash, safe_error)
            broadcast_errors.append(f"{rpc_label}: {safe_error}")
            if self._is_known_transaction_error(exc):
                lookup = self._lookup_tx_with_fallback(signed_tx_hash)
                if lookup.get("receipt") is not None:
                    receipt = lookup["receipt"]
                    tx_hash = receipt.get("transactionHash")
                    metadata["broadcast_rpc"] = rpc_label
                    metadata["receipt_rpc"] = lookup.get("rpc_label")
                    metadata["known_transaction_recovered"] = True
                else:
                    accepted_pending = True
                    metadata["broadcast_rpc"] = rpc_label
                    metadata["receipt_rpc"] = lookup.get("rpc_label")
                    metadata["known_transaction_pending"] = True
            log_block(
                log,
                logging.WARNING,
                f"{label} broadcast failed",
                pool_context(self.pool),
                {
                    "stage": "broadcast",
                    "action": label,
                    "status": "KNOWN_TRANSACTION" if accepted_pending or receipt is not None else "FAILED",
                    "rpc": rpc_label,
                    "signed_tx_hash": signed_tx_hash,
                    "reason": safe_error,
                    "next_action": (
                        "wait for public receipt lookup"
                        if accepted_pending or receipt is not None
                        else "stop without public RPC fallback"
                    ),
                },
            )
            if label != "mint" and receipt is None:
                raise RuntimeError(
                    f"{label} protected broadcast failed: {safe_error}"
                ) from None
        if accepted_pending:
            return TxResult(
                tx_hash=signed_tx_hash,
                status="PENDING",
                metadata={
                    **metadata,
                    "error": f"{label} transaction known by RPC but receipt is not available",
                    "broadcast_errors": broadcast_errors,
                },
            )
        if tx_hash is None:
            if label != "mint":
                raise RuntimeError("transaction broadcast failed")
            log_block(
                log,
                logging.WARNING,
                f"{label} broadcast unknown",
                pool_context(self.pool),
                {
                    "stage": "broadcast",
                    "action": label,
                    "status": "BROADCAST_UNKNOWN",
                    "signed_tx_hash": signed_tx_hash,
                    "reason": "; ".join(broadcast_errors[-3:]) or "broadcast failed",
                    "next_action": "journal recovery will treat signed hash as local-only unless found on-chain",
                },
            )
            return TxResult(
                tx_hash=signed_tx_hash,
                status="BROADCAST_UNKNOWN",
                metadata={
                    **metadata,
                    "error": "; ".join(broadcast_errors[-3:]) or "broadcast failed",
                    "broadcast_errors": broadcast_errors,
                },
            )
        tx_hash_hex = self._hex_value(tx_hash)
        if not tx_hash_hex.startswith("0x"):
            tx_hash_hex = "0x" + tx_hash_hex
        metadata["broadcast_tx_hash"] = tx_hash_hex
        log_block(
            log,
            logging.INFO,
            f"{label} broadcast accepted",
            pool_context(self.pool),
            {
                "stage": "broadcast_accepted",
                "action": label,
                "tx_hash": tx_hash_hex,
                "signed_tx_hash": signed_tx_hash,
                "nonce": metadata.get("nonce"),
                "rpc": metadata.get("broadcast_rpc"),
            },
        )
        if receipt is None:
            try:
                receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
            except TimeExhausted as exc:
                self.record_guard_error(signed_tx_hash, str(exc))
                if label != "mint":
                    raise
                log_block(
                    log,
                    logging.WARNING,
                    f"{label} receipt timeout",
                    pool_context(self.pool),
                    {
                        "stage": "receipt_wait",
                        "action": label,
                        "status": "PENDING",
                        "tx_hash": tx_hash_hex,
                        "signed_tx_hash": signed_tx_hash,
                        "rpc": metadata.get("broadcast_rpc"),
                        "reason": exc,
                        "next_action": "journal recovery will try receipt lookup",
                    },
                )
                return TxResult(
                    tx_hash=tx_hash_hex,
                    status="PENDING",
                    metadata={**metadata, "error": str(exc)},
                )
            except Exception as exc:
                self.record_guard_error(signed_tx_hash, safe_rpc_error(exc))
                raise
        if receipt["status"] != 1:
            self.clear_wallet_guard(signed_tx_hash)
            raise RuntimeError(f"{label} reverted: {tx_hash_hex}")
        effective_gas_price = int(receipt.get("effectiveGasPrice") or gas_params["maxFeePerGas"])
        receipt_block = int(receipt.get("blockNumber") or 0)
        gas_used = int(receipt["gasUsed"])
        gas_price_gwei = float(Web3.from_wei(effective_gas_price, "gwei"))
        self.clear_wallet_guard(signed_tx_hash)
        log_block(
            log,
            logging.INFO,
            f"{label} receipt",
            pool_context(self.pool),
            {
                "stage": "receipt",
                "action": label,
                "status": receipt.get("status"),
                "tx_hash": tx_hash_hex,
                "block": receipt_block,
                "gas_used": gas_used,
                "effective_gas_price_gwei": f"{gas_price_gwei:.9f}",
            },
        )
        return TxResult(
            tx_hash=tx_hash_hex,
            gas_used=gas_used,
            gas_price_gwei=gas_price_gwei,
            metadata={**metadata, "receipt_block": receipt_block},
        )

    def _read_attempts(self) -> list[tuple[str, Web3]]:
        attempts = [("primary", self.w3)]
        current_url = getattr(getattr(self.w3, "provider", None), "endpoint_uri", None)
        seen = {current_url} if current_url else set()
        from .chain_config import RPC_BACKUP_LIST, RPC_URLS_2

        urls = [RPC_URLS_2.get(self.pool.chain)] + RPC_BACKUP_LIST.get(self.pool.chain, [])
        fallback_index = 1
        for url in [item for item in urls if item]:
            if url in seen:
                continue
            seen.add(url)
            attempts.append((f"backup-{fallback_index}:{self._rpc_label(url)}", self._web3_for_rpc(url)))
            fallback_index += 1
        return attempts

    def sign_transaction(self, wallet: str, tx: dict):
        if self.signer is None:
            raise RuntimeError("runtime signer is required for live transactions")
        return self.signer.sign_transaction(wallet, tx)

    def _lookup_tx_with_fallback(self, tx_hash: str) -> dict:
        for rpc_label, candidate_w3 in self._read_attempts():
            try:
                receipt = candidate_w3.eth.get_transaction_receipt(tx_hash)
                return {"receipt": receipt, "tx_found": True, "rpc_label": rpc_label, "w3": candidate_w3}
            except TransactionNotFound:
                pass
            except Exception:
                pass
            try:
                candidate_w3.eth.get_transaction(tx_hash)
                return {"receipt": None, "tx_found": True, "rpc_label": rpc_label, "w3": candidate_w3}
            except TransactionNotFound:
                continue
            except Exception:
                continue
        return {"receipt": None, "tx_found": False}

    def _web3_for_rpc(self, url: str) -> Web3:
        candidate = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 30}))
        if self.pool.chain.upper() == "BNB":
            try:
                from web3.middleware import ExtraDataToPOAMiddleware

                candidate.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            except ImportError:
                from web3.middleware import geth_poa_middleware

                candidate.middleware_onion.inject(geth_poa_middleware, layer=0)
        return candidate

    @staticmethod
    def _is_known_transaction_error(exc: Exception) -> bool:
        text = str(exc).lower()
        return "already known" in text or "known transaction" in text or "already imported" in text

    def write_rpc_label(self) -> str:
        endpoint = getattr(getattr(self.write_w3, "provider", None), "endpoint_uri", None)
        return self._rpc_label(endpoint) if endpoint else "protected"

    @staticmethod
    def _rpc_label(url: str) -> str:
        parsed = urlparse(url)
        return parsed.netloc or "unknown-rpc"

    @staticmethod
    def _hex_value(value) -> str:
        if value is None:
            return "0x"
        if isinstance(value, str):
            text = value
        elif hasattr(value, "hex"):
            text = value.hex()
        else:
            text = str(value)
        if text and not text.startswith("0x"):
            text = "0x" + text
        return text

    def gas_policy(self) -> GasPolicy:
        chain = self.pool.chain.upper()
        if self.worker_config and chain in self.worker_config.gas_policies:
            return self.worker_config.gas_policies[chain]
        return DEFAULT_GAS_POLICIES.get(chain) or GasPolicy()

    def _validate_protected_transaction(self, tx: dict) -> None:
        if get_chain_id(self.pool.chain) != 1:
            return
        if int(tx.get("maxPriorityFeePerGas") or 0) <= 0:
            raise RuntimeError("Flashbots Protect requires maxPriorityFeePerGas greater than zero")

    def _safe_tx_metadata(self, label: str, tx: dict, gas_params: dict) -> dict:
        return {
            "label": label,
            "chain_id": tx.get("chainId"),
            "nonce": tx.get("nonce"),
            "gas_limit": tx.get("gas"),
            "from": tx.get("from"),
            "to": tx.get("to"),
            "value": str(tx.get("value") or 0),
            "data_length": len(str(tx.get("data") or "")),
            "max_fee_per_gas_gwei": float(Web3.from_wei(gas_params["maxFeePerGas"], "gwei")),
            "max_priority_fee_per_gas_gwei": float(Web3.from_wei(gas_params["maxPriorityFeePerGas"], "gwei")),
        }
