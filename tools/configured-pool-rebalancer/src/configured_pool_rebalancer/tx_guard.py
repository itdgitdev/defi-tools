from __future__ import annotations

from datetime import datetime, timezone
from typing import Iterable

import mysql.connector
from web3 import Web3
from web3.exceptions import TransactionNotFound

from .evm import normalize_chain


from .database import get_connection


class ActiveWalletTransactionError(RuntimeError):
    def __init__(self, guard: dict):
        self.guard = guard
        super().__init__(
            "wallet transaction guard is active "
            f"chain={guard.get('chain')} wallet={guard.get('wallet_address')} "
            f"nonce={guard.get('nonce')} action={guard.get('action')} "
            f"signed_hash={guard.get('signed_tx_hash')}"
        )


class WalletTxGuard:
    TABLE = "configured_wallet_tx_guards"

    def migrate(self) -> None:
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.TABLE} (
                    chain VARCHAR(10) NOT NULL,
                    wallet_address VARCHAR(42) NOT NULL,
                    nonce BIGINT UNSIGNED NOT NULL,
                    signed_tx_hash VARCHAR(66) NOT NULL,
                    broadcast_tx_hash VARCHAR(66) NULL,
                    action VARCHAR(40) NOT NULL,
                    pool_name VARCHAR(160) NULL,
                    last_error VARCHAR(500) NULL,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    PRIMARY KEY (chain, wallet_address),
                    UNIQUE KEY uniq_wallet_guard_signed_hash (signed_tx_hash)
                )
                """
            )
            conn.commit()
        finally:
            cursor.close()
            conn.close()

    def get_active(self, chain: str, wallet: str) -> dict | None:
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                f"""
                SELECT chain, wallet_address, nonce, signed_tx_hash,
                       broadcast_tx_hash, action, pool_name, last_error,
                       created_at, updated_at
                FROM {self.TABLE}
                WHERE chain=%s AND wallet_address=%s
                LIMIT 1
                """,
                self._key(chain, wallet),
            )
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception as exc:
            raise self._db_error("read", exc) from exc
        finally:
            cursor.close()
            conn.close()

    def reserve(
        self,
        chain: str,
        wallet: str,
        nonce: int,
        signed_hash: str,
        action: str,
        pool_name: str | None,
    ) -> None:
        now = self._now()
        normalized_chain, normalized_wallet = self._key(chain, wallet)
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"""
                INSERT INTO {self.TABLE} (
                    chain, wallet_address, nonce, signed_tx_hash,
                    action, pool_name, created_at, updated_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (
                    normalized_chain,
                    normalized_wallet,
                    int(nonce),
                    self._hash(signed_hash),
                    str(action)[:40],
                    str(pool_name)[:160] if pool_name else None,
                    now,
                    now,
                ),
            )
            conn.commit()
        except mysql.connector.IntegrityError as exc:
            conn.rollback()
            active = self.get_active(normalized_chain, normalized_wallet)
            if active:
                raise ActiveWalletTransactionError(active) from exc
            raise self._db_error("reserve", exc) from exc
        except Exception as exc:
            conn.rollback()
            raise self._db_error("reserve", exc) from exc
        finally:
            cursor.close()
            conn.close()

    def record_broadcast(
        self,
        chain: str,
        wallet: str,
        signed_hash: str,
        broadcast_hash: str,
    ) -> None:
        self._update(
            chain,
            wallet,
            signed_hash,
            "broadcast_tx_hash=%s, updated_at=%s",
            (self._hash(broadcast_hash), self._now()),
            "record broadcast",
        )

    def record_error(
        self,
        chain: str,
        wallet: str,
        signed_hash: str,
        error: str,
    ) -> None:
        self._update(
            chain,
            wallet,
            signed_hash,
            "last_error=%s, updated_at=%s",
            (str(error)[:500], self._now()),
            "record error",
        )

    def clear(self, chain: str, wallet: str, signed_hash: str) -> bool:
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"""
                DELETE FROM {self.TABLE}
                WHERE chain=%s AND wallet_address=%s AND signed_tx_hash=%s
                """,
                (*self._key(chain, wallet), self._hash(signed_hash)),
            )
            deleted = int(cursor.rowcount or 0) == 1
            conn.commit()
            return deleted
        except Exception as exc:
            conn.rollback()
            raise self._db_error("clear", exc) from exc
        finally:
            cursor.close()
            conn.close()

    def list_active(
        self,
        chain: str | None = None,
        wallet: str | None = None,
    ) -> list[dict]:
        clauses: list[str] = []
        params: list[str] = []
        if chain:
            clauses.append("chain=%s")
            params.append(normalize_chain(chain))
        if wallet:
            clauses.append("wallet_address=%s")
            params.append(self._wallet(wallet))
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        conn = get_connection()
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute(
                f"""
                SELECT chain, wallet_address, nonce, signed_tx_hash,
                       broadcast_tx_hash, action, pool_name, last_error,
                       created_at, updated_at
                FROM {self.TABLE}{where}
                ORDER BY chain, wallet_address
                """,
                tuple(params),
            )
            return [dict(row) for row in cursor.fetchall()]
        except Exception as exc:
            raise self._db_error("list", exc) from exc
        finally:
            cursor.close()
            conn.close()

    def reconcile_receipt(
        self,
        chain: str,
        wallet: str,
        public_rpc_attempts: Iterable[tuple[str, Web3]],
    ) -> dict | None:
        active = self.get_active(chain, wallet)
        if active is None:
            return None
        tx_hash = active["signed_tx_hash"]
        for label, candidate in public_rpc_attempts:
            try:
                receipt = candidate.eth.get_transaction_receipt(tx_hash)
            except TransactionNotFound:
                continue
            except Exception:
                continue
            if receipt is None:
                continue
            try:
                receipt_status = int(receipt["status"])
            except (KeyError, TypeError, ValueError):
                continue
            if receipt_status not in {0, 1}:
                continue
            if not self.clear(chain, wallet, tx_hash):
                raise RuntimeError(
                    "wallet transaction guard changed before receipt reconciliation"
                )
            return {
                "guard": active,
                "receipt": receipt,
                "rpc_label": label,
            }
        raise ActiveWalletTransactionError(active)

    def _update(
        self,
        chain: str,
        wallet: str,
        signed_hash: str,
        assignments: str,
        values: tuple,
        operation: str,
    ) -> None:
        conn = get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                f"""
                UPDATE {self.TABLE}
                SET {assignments}
                WHERE chain=%s AND wallet_address=%s AND signed_tx_hash=%s
                """,
                (*values, *self._key(chain, wallet), self._hash(signed_hash)),
            )
            if int(cursor.rowcount or 0) != 1:
                raise RuntimeError(
                    "wallet transaction guard does not match signed transaction"
                )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            if isinstance(exc, RuntimeError):
                raise
            raise self._db_error(operation, exc) from exc
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def _key(chain: str, wallet: str) -> tuple[str, str]:
        return normalize_chain(chain), WalletTxGuard._wallet(wallet)

    @staticmethod
    def _wallet(wallet: str) -> str:
        return Web3.to_checksum_address(wallet).lower()

    @staticmethod
    def _hash(value: str) -> str:
        normalized = Web3.to_hex(hexstr=str(value))
        if len(normalized) != 66:
            raise ValueError("transaction hash must contain exactly 32 bytes")
        return normalized.lower()

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc).replace(tzinfo=None)

    @staticmethod
    def _db_error(operation: str, exc: Exception) -> RuntimeError:
        return RuntimeError(
            f"wallet transaction guard {operation} failed; "
            "run configured rebalancer with --migrate before live execution "
            f"({type(exc).__name__})"
        )
