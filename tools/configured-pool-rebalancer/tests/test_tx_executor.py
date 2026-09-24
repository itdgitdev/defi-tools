from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from web3 import Web3

from configured_pool_rebalancer.models import DexType, PoolConfig, WorkerConfig
from configured_pool_rebalancer.tx_executor import TxExecutor


class FakeCall:
    def build_transaction(self, base_tx):
        return {**base_tx, "to": "0x0000000000000000000000000000000000000005", "data": "0x1234"}


class FakeTxHash(bytes):
    def hex(self):
        return "0x" + super().hex()


def make_pool():
    return PoolConfig(
        name="TEST",
        chain="BASE",
        pool_address="0x0000000000000000000000000000000000000001",
        dex_type=DexType.PANCAKE_V3_MASTERCHEF,
        managed_wallets=("0x0000000000000000000000000000000000000002",),
        bot_wallet="0x0000000000000000000000000000000000000002",
    )


class FakeSigner:
    def __init__(self, raw_transaction=b"signed-mint"):
        self.raw_transaction = raw_transaction
        self.calls = []

    def sign_transaction(self, wallet, tx):
        self.calls.append((wallet, tx))
        return SimpleNamespace(raw_transaction=self.raw_transaction)


class TxExecutorTests(unittest.TestCase):
    def test_live_run_requires_runtime_signer(self):
        pool = make_pool()
        w3 = SimpleNamespace()

        with self.assertRaisesRegex(RuntimeError, "runtime signer is required"):
            TxExecutor(w3, pool, dry_run=False, worker_config=WorkerConfig(pools=(pool,)))

    def test_dry_run_does_not_require_runtime_signer(self):
        pool = make_pool()
        w3 = SimpleNamespace()

        result = TxExecutor(w3, pool, dry_run=True, worker_config=WorkerConfig(pools=(pool,))).send(
            FakeCall(),
            "mint",
            gas=700000,
        )

        self.assertTrue(result.dry_run)
        self.assertEqual(result.tx_hash, "dry-run:mint")

    def test_broadcast_error_after_signing_returns_broadcast_unknown(self):
        pool = make_pool()
        w3 = SimpleNamespace()
        w3.eth = SimpleNamespace()
        w3.eth.get_transaction_count = lambda wallet, block: 7
        w3.eth.send_raw_transaction = lambda raw: (_ for _ in ()).throw(RuntimeError("rpc invalid params"))
        signer = FakeSigner()
        expected_hash = Web3.keccak(b"signed-mint").hex()
        if not expected_hash.startswith("0x"):
            expected_hash = "0x" + expected_hash

        def run():
            with (
                patch(
                    "configured_pool_rebalancer.tx_executor.get_gas_params",
                    return_value={"maxFeePerGas": Web3.to_wei(0.01, "gwei"), "maxPriorityFeePerGas": 1},
                ),
                patch("configured_pool_rebalancer.tx_executor.validate_gas_cap"),
                patch("configured_pool_rebalancer.tx_executor.get_chain_id", return_value=8453),
            ):
                return TxExecutor(w3, pool, dry_run=False, worker_config=WorkerConfig(pools=(pool,)), signer=signer, write_w3=w3, tx_guard=Mock()).send(
                    FakeCall(),
                    "mint",
                    gas=700000,
                )

        result = run()

        self.assertEqual(result.status, "BROADCAST_UNKNOWN")
        self.assertEqual(result.tx_hash, expected_hash)
        self.assertEqual(result.metadata["signed_tx_hash"], expected_hash)
        self.assertEqual(result.metadata["nonce"], 7)
        self.assertIn("rpc invalid params", result.metadata["error"])
        self.assertEqual(len(signer.calls), 1)

    def test_mint_broadcast_uses_protected_write_rpc(self):
        pool = make_pool()
        tx_hash = FakeTxHash(bytes.fromhex("12" * 32))
        primary = SimpleNamespace()
        primary.eth = SimpleNamespace()
        primary.eth.get_transaction_count = lambda wallet, block: 7
        primary.eth.send_raw_transaction = lambda raw: (_ for _ in ()).throw(RuntimeError("rpc internal error"))
        signer = FakeSigner()

        backup = SimpleNamespace()
        backup.eth = SimpleNamespace()
        backup.provider = SimpleNamespace(endpoint_uri="https://backup")
        backup.eth.send_raw_transaction = lambda raw: tx_hash
        primary.eth.wait_for_transaction_receipt = lambda got_hash, timeout: {
            "status": 1, "transactionHash": got_hash, "blockNumber": 123,
            "gasUsed": 456, "effectiveGasPrice": Web3.to_wei(0.006, "gwei"),
        }
        backup.eth.wait_for_transaction_receipt = lambda got_hash, timeout: {
            "status": 1,
            "transactionHash": got_hash,
            "blockNumber": 123,
            "gasUsed": 456,
            "effectiveGasPrice": Web3.to_wei(0.006, "gwei"),
        }

        def run():
            with (
                patch(
                    "configured_pool_rebalancer.tx_executor.get_gas_params",
                    return_value={"maxFeePerGas": Web3.to_wei(0.01, "gwei"), "maxPriorityFeePerGas": 1},
                ),
                patch("configured_pool_rebalancer.tx_executor.validate_gas_cap"),
                patch("configured_pool_rebalancer.tx_executor.get_chain_id", return_value=8453),
                patch("configured_pool_rebalancer.chain_config.RPC_URLS_2", {"BASE": "http://backup"}),
                patch("configured_pool_rebalancer.chain_config.RPC_BACKUP_LIST", {"BASE": []}),
                patch.object(TxExecutor, "_web3_for_rpc", return_value=backup),
            ):
                return TxExecutor(
                    primary,
                    pool,
                    dry_run=False,
                    worker_config=WorkerConfig(pools=(pool,)),
                    signer=signer,
                    write_w3=backup,
                    tx_guard=Mock(),
                ).send(
                    FakeCall(),
                    "mint",
                    gas=700000,
                )

        result = run()

        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(result.tx_hash, tx_hash.hex())
        self.assertEqual(result.metadata["broadcast_rpc"], "backup")
        self.assertEqual(result.metadata["broadcast_tx_hash"], tx_hash.hex())

    def test_mint_already_known_recovers_receipt_instead_of_broadcast_unknown(self):
        pool = make_pool()
        tx_hash = "0x" + Web3.keccak(b"signed-mint").hex().replace("0x", "")
        receipt = {
            "status": 1,
            "transactionHash": tx_hash,
            "blockNumber": 123,
            "gasUsed": 456,
            "effectiveGasPrice": Web3.to_wei(0.006, "gwei"),
        }
        primary = SimpleNamespace()
        primary.eth = SimpleNamespace()
        primary.eth.get_transaction_count = lambda wallet, block: 7
        primary.eth.send_raw_transaction = lambda raw: (_ for _ in ()).throw(RuntimeError("already known"))
        primary.eth.get_transaction_receipt = lambda got_hash: receipt
        signer = FakeSigner()

        def run():
            with (
                patch(
                    "configured_pool_rebalancer.tx_executor.get_gas_params",
                    return_value={"maxFeePerGas": Web3.to_wei(0.01, "gwei"), "maxPriorityFeePerGas": 1},
                ),
                patch("configured_pool_rebalancer.tx_executor.validate_gas_cap"),
                patch("configured_pool_rebalancer.tx_executor.get_chain_id", return_value=8453),
            ):
                return TxExecutor(
                    primary,
                    pool,
                    dry_run=False,
                    worker_config=WorkerConfig(pools=(pool,)),
                    signer=signer,
                    write_w3=primary,
                    tx_guard=Mock(),
                ).send(
                    FakeCall(),
                    "mint",
                    gas=700000,
                )

        result = run()

        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(result.tx_hash, tx_hash)
        self.assertTrue(result.metadata["known_transaction_recovered"])


if __name__ == "__main__":
    unittest.main()
