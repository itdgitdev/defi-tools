from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import patch

from configured_pool_rebalancer.discord_notifier import DiscordNotifier
from configured_pool_rebalancer.models import (
    DexType,
    PoolConfig,
    PositionSnapshot,
    RebalancePlan,
    Slot0,
    TokenBalance,
    TxResult,
    WorkerConfig,
)
from configured_pool_rebalancer.worker import ConfiguredPoolRebalancer
from configured_pool_rebalancer.worker import TRANSFER_TOPIC


TOKEN0 = "0x0000000000000000000000000000000000000003"
TOKEN1 = "0x0000000000000000000000000000000000000004"
WALLET = "0x0000000000000000000000000000000000000002"


def make_pool() -> PoolConfig:
    return PoolConfig(
        name="TEST-USDC-POP",
        chain="BASE",
        pool_address="0x0000000000000000000000000000000000000001",
        dex_type=DexType.PANCAKE_V3_MASTERCHEF,
        managed_wallets=(WALLET,),
        bot_wallet=WALLET,
        token0_address=TOKEN0,
        token1_address=TOKEN1,
        token0_decimals=6,
        token1_decimals=18,
        fee=2500,
        pid=1,
    )


def make_worker(discord_enabled: bool = False):
    worker = ConfiguredPoolRebalancer.__new__(ConfiguredPoolRebalancer)
    worker.config = WorkerConfig(pools=(make_pool(),), dry_run=False, discord_enabled=discord_enabled)
    return worker


class FakeJournal:
    def __init__(self, reservations=None, already_notified=False):
        self.reservations = reservations or []
        self.already_notified = already_notified
        self.errors = []
        self.notified = []
        self.partial_notified = []
        self.partial_errors = []
        self.plans = []
        self.statuses = []
        self.recorded_reservations = []
        self.snapshots = []

    def fetch_wallet_token_reservations(self, chain, wallet):
        return self.reservations

    def create_or_update_plan(self, chain, pool_address, wallet, plan):
        self.plans.append((chain, pool_address, wallet, plan))

    def mark_status(self, chain, old_token_id, status, tx_label=None, tx_result=None, *args, **kwargs):
        self.statuses.append((chain, old_token_id, status, tx_label, tx_result, args, kwargs))

    def record_reservation(self, chain, old_token_id, token0_address, token1_address, reserved0_raw, reserved1_raw):
        self.recorded_reservations.append(
            (chain, old_token_id, token0_address, token1_address, reserved0_raw, reserved1_raw)
        )

    def record_balance_snapshot(self, chain, old_token_id, stage, balance0_raw, balance1_raw):
        self.snapshots.append((chain, old_token_id, stage, balance0_raw, balance1_raw))

    def mark_recovery_error(self, chain, old_token_id, error_reason):
        self.errors.append((chain, old_token_id, error_reason))

    def recovery_already_notified(self, chain, old_token_id):
        return self.already_notified

    def mark_recovery_notified(self, chain, old_token_id):
        self.already_notified = True
        self.notified.append((chain, old_token_id))

    def partial_already_notified(self, chain, old_token_id, notify_key):
        return (chain, old_token_id, notify_key) in self.partial_notified

    def mark_discord_partial_notified(self, chain, old_token_id, notify_key):
        self.partial_notified.append((chain, old_token_id, notify_key))

    def mark_discord_partial_error(self, chain, old_token_id, error):
        self.partial_errors.append((chain, old_token_id, error))

    def clear_recovery_error(self, chain, old_token_id):
        self.errors.append((chain, old_token_id, None))

    def clear_mint_tx_for_retry(self, chain, old_token_id, reason):
        self.statuses.append((chain, old_token_id, "CLEAR_MINT_TX", None, None, (), {"error_reason": reason}))


class FakeAdapter:
    def __init__(self, balance0: int, balance1: int):
        self.balance0 = TokenBalance(raw=balance0, decimals=6)
        self.balance1 = TokenBalance(raw=balance1, decimals=18)

    def read_balances(self, wallet):
        return self.balance0, self.balance1

    def read_slot0(self):
        return Slot0(sqrt_price_x96=2**96, tick=0)

    def mint(self, plan):
        raise AssertionError("mint should not be called")


class FakeRecoveredMintAdapter:
    masterchef_address = "0x0000000000000000000000000000000000000005"

    def __init__(self, pool: PoolConfig):
        self.pool = pool
        self.staked = []

    def _mint_receipt_from_result(self, result):
        return {
            "status": 1,
            "transactionHash": result.tx_hash,
            "gasUsed": 12345,
            "effectiveGasPrice": 100,
            "blockNumber": 99,
        }

    def _new_token_id_from_mint_receipt(self, receipt):
        return 456

    def _minted_position_matches_plan(self, token_id, plan):
        return token_id == 456 and plan.new_tick_lower == -50 and plan.new_tick_upper == 50

    def read_npm_position(self, token_id, owner=None):
        return PositionSnapshot(
            token_id=token_id,
            owner=self.pool.bot_wallet,
            pool_address=self.pool.pool_address,
            token0=self.pool.token0_address,
            token1=self.pool.token1_address,
            fee=self.pool.fee,
            tick_lower=-50,
            tick_upper=50,
            liquidity=1,
        )

    def stake(self, token_id):
        self.staked.append(token_id)
        return TxResult(tx_hash="0x" + "2" * 64, status="SUCCESS")

    def read_staked_positions(self, token_ids):
        return {token_id: True for token_id in token_ids if token_id in self.staked}

    def burn_if_empty_and_owned(self, token_id):
        return None


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def recovery_required_message(self, pool_name, chain, wallet, token_id, reason):
        return f"{pool_name}|{chain}|{wallet}|{token_id}|{reason}"

    def partial_action_message(
        self,
        pool_name,
        chain,
        wallet,
        old_token_id,
        action,
        status,
        reason,
        next_action,
        tx_hash=None,
        signed_tx_hash=None,
        new_token_id=None,
    ):
        return f"{pool_name}|{chain}|{wallet}|{old_token_id}|{new_token_id}|{action}|{status}|{tx_hash}|{signed_tx_hash}|{reason}|{next_action}"

    def send(self, message):
        self.sent.append(message)


class FakeW3:
    def __init__(self, receipt):
        self.eth = self
        self.receipt = receipt

    def get_transaction_receipt(self, tx_hash):
        if isinstance(self.receipt, Exception):
            raise self.receipt
        return self.receipt


class ReservationLedgerTests(unittest.TestCase):
    def test_receipt_token_inflows_parse_transfer_logs_to_wallet(self):
        worker = make_worker()
        pool = make_pool()
        receipt = {
            "logs": [
                {
                    "address": pool.token0_address,
                    "topics": [
                        TRANSFER_TOPIC,
                        self._topic_address("0x00000000000000000000000000000000000000aa"),
                        self._topic_address(pool.bot_wallet),
                    ],
                    "data": self._uint_data(123),
                },
                {
                    "address": pool.token1_address,
                    "topics": [
                        TRANSFER_TOPIC,
                        self._topic_address("0x00000000000000000000000000000000000000bb"),
                        self._topic_address(pool.bot_wallet),
                    ],
                    "data": self._uint_data(456),
                },
                {
                    "address": pool.token0_address,
                    "topics": [
                        TRANSFER_TOPIC,
                        self._topic_address("0x00000000000000000000000000000000000000cc"),
                        self._topic_address("0x00000000000000000000000000000000000000dd"),
                    ],
                    "data": self._uint_data(999),
                },
            ]
        }

        self.assertEqual(worker._token_inflows_from_receipt(receipt, pool, pool.bot_wallet), (123, 456))

    def test_receipt_token_movements_parse_sent_and_received(self):
        worker = make_worker()
        pool = make_pool()
        receipt = {
            "logs": [
                {
                    "address": pool.token0_address,
                    "topics": [
                        TRANSFER_TOPIC,
                        self._topic_address(pool.bot_wallet),
                        self._topic_address("0x00000000000000000000000000000000000000aa"),
                    ],
                    "data": self._uint_data(100),
                },
                {
                    "address": pool.token0_address,
                    "topics": [
                        TRANSFER_TOPIC,
                        self._topic_address(pool.bot_wallet),
                        self._topic_address("0x00000000000000000000000000000000000000bb"),
                    ],
                    "data": self._uint_data(25),
                },
                {
                    "address": pool.token1_address,
                    "topics": [
                        TRANSFER_TOPIC,
                        self._topic_address("0x00000000000000000000000000000000000000cc"),
                        self._topic_address(pool.bot_wallet),
                    ],
                    "data": self._uint_data(300),
                },
            ]
        }

        self.assertEqual(worker._token_movements_from_receipt(receipt, pool, pool.bot_wallet), (125, 0, 0, 300))

    def test_reservation_after_swap_moves_input_to_output_token(self):
        worker = make_worker()
        pool = make_pool()
        swap_tx = TxResult(
            tx_hash="0x" + "1" * 64,
            metadata={
                "token_in": pool.token1_address,
                "token_out": pool.token0_address,
                "amount_in": "30",
            },
        )

        reserved0, reserved1 = worker._reservation_after_swap(
            pool,
            reserved0=10,
            reserved1=100,
            before0=TokenBalance(raw=1000, decimals=6),
            before1=TokenBalance(raw=1000, decimals=18),
            after0=TokenBalance(raw=1025, decimals=6),
            after1=TokenBalance(raw=970, decimals=18),
            swap_tx=swap_tx,
        )

        self.assertEqual((reserved0, reserved1), (35, 70))

    def test_reservation_after_swap_prefers_receipt_output_over_balance_delta(self):
        worker = make_worker()
        pool = make_pool()
        swap_tx = TxResult(
            tx_hash="0x" + "1" * 64,
            metadata={
                "token_in": pool.token1_address,
                "token_out": pool.token0_address,
                "amount_in": "30",
            },
        )

        reserved0, reserved1 = worker._reservation_after_swap(
            pool,
            reserved0=10,
            reserved1=100,
            before0=TokenBalance(raw=1000, decimals=6),
            before1=TokenBalance(raw=1000, decimals=18),
            after0=TokenBalance(raw=1001, decimals=6),
            after1=TokenBalance(raw=970, decimals=18),
            swap_tx=swap_tx,
            receipt_inflows=(25, 0),
        )

        self.assertEqual((reserved0, reserved1), (35, 70))

    def test_swap_receipt_confirms_expected_output_token(self):
        worker = make_worker()
        pool = make_pool()
        swap_tx = TxResult(
            tx_hash="0x" + "1" * 64,
            metadata={
                "token_in": pool.token1_address,
                "token_out": pool.token0_address,
                "amount_in": "30",
            },
        )

        self.assertTrue(worker._swap_receipt_confirms_output(pool, swap_tx, (25, 0)))
        self.assertFalse(worker._swap_receipt_confirms_output(pool, swap_tx, (0, 25)))
        self.assertFalse(worker._swap_receipt_confirms_output(pool, swap_tx, None))

    def test_mint_amount_is_clamped_by_job_reservation(self):
        worker = make_worker()
        plan = RebalancePlan(
            old_token_id=123,
            current_tick=0,
            old_tick_lower=-100,
            old_tick_upper=100,
            new_tick_lower=-50,
            new_tick_upper=50,
            amount0_desired=100,
            amount1_desired=100,
        )

        original0, original1, available0, available1 = worker._clamp_plan_to_reservation(
            plan,
            reserved0=35,
            reserved1=70,
            pre_mint0=TokenBalance(raw=1000, decimals=6),
            pre_mint1=TokenBalance(raw=1000, decimals=18),
        )

        self.assertEqual((original0, original1), (100, 100))
        self.assertEqual((available0, available1), (35, 70))
        self.assertEqual((plan.amount0_desired, plan.amount1_desired), (35, 70))

    def test_reservation_coverage_blocks_when_wallet_cannot_cover_total(self):
        worker = make_worker()
        pool = make_pool()
        worker.journal = FakeJournal(
            reservations=[
                {
                    "token_address": pool.token0_address.lower(),
                    "reserved_raw": "120",
                }
            ]
        )

        error = worker._reservation_coverage_error(
            pool,
            FakeAdapter(balance0=100, balance1=1000),
            stage="recovery_pre_mint",
        )

        self.assertIsNotNone(error)
        self.assertIn("reservation coverage failed", error)
        self.assertIn("required_raw=120", error)
        self.assertIn("actual_raw=100", error)

    def test_reservation_coverage_allows_shared_token_when_wallet_can_cover_total(self):
        worker = make_worker()
        pool = make_pool()
        worker.journal = FakeJournal(
            reservations=[
                {
                    "token_address": pool.token0_address.lower(),
                    "reserved_raw": "120",
                }
            ]
        )

        error = worker._reservation_coverage_error(
            pool,
            FakeAdapter(balance0=130, balance1=1000),
            stage="recovery_pre_mint",
        )

        self.assertIsNone(error)

    def test_recovery_requires_manual_action_when_reservation_is_missing(self):
        worker = make_worker()
        pool = make_pool()
        worker.journal = FakeJournal()
        worker.notifier = FakeNotifier()

        result = worker._recover_withdrawn_unminted(
            w3=None,
            pool=pool,
            adapter=None,
            job={
                "old_token_id": 123,
                "status": "FAILED",
                "wallet_address": pool.bot_wallet,
                "old_tick_lower": -100,
                "old_tick_upper": 100,
            },
        )

        self.assertEqual(result["state"], "RECOVERY_REQUIRED")
        self.assertEqual(result["recovery"], "MANUAL_REQUIRED")
        self.assertIn("missing reservation ledger", worker.journal.errors[0][2])
        self.assertEqual(worker.journal.statuses[0][2].value, "RECOVERY_REQUIRED")

    def test_recovery_dust_swap_required_blocks_mint(self):
        worker = make_worker()
        pool = replace(
            make_pool(),
            rebalance_range_mode="price_percent",
            rebalance_range_lower_percent=-5.0,
            rebalance_range_upper_percent=10.0,
        )
        worker.journal = FakeJournal()
        worker.notifier = FakeNotifier()
        worker._swap_dust_reason = lambda *args, **kwargs: "swap input below dust threshold"
        position = PositionSnapshot(
            token_id=123,
            owner=pool.bot_wallet,
            pool_address=pool.pool_address,
            token0=pool.token0_address,
            token1=pool.token1_address,
            fee=pool.fee,
            tick_lower=-100,
            tick_upper=100,
            liquidity=1,
        )
        plan = RebalancePlan(
            old_token_id=123,
            current_tick=0,
            old_tick_lower=-100,
            old_tick_upper=100,
            new_tick_lower=-50,
            new_tick_upper=50,
            amount0_desired=0,
            amount1_desired=100,
            swap_token_in=pool.token1_address,
            swap_token_out=pool.token0_address,
            swap_amount_in=10,
        )

        class FakeSwapPlanner:
            def build_swap_plan(self, *args, **kwargs):
                return plan

        with patch("configured_pool_rebalancer.worker.SwapPlanner", return_value=FakeSwapPlanner()):
            result = worker._resume_mint_from_reservation(
                w3=None,
                pool=pool,
                adapter=FakeAdapter(balance0=0, balance1=100),
                position=position,
                reserved0=0,
                reserved1=100,
                allow_swap=True,
            )

        self.assertEqual(result["state"], "RECOVERY_REQUIRED")
        self.assertIn("dust swap required before mint", result["error"])

    def test_recovery_existing_swap_reconciles_reservation_and_resumes_without_swap(self):
        worker = make_worker()
        pool = make_pool()
        worker.journal = FakeJournal()
        worker.notifier = FakeNotifier()
        captured = {}
        receipt = {
            "status": 1,
            "logs": [
                {
                    "address": pool.token0_address,
                    "topics": [
                        TRANSFER_TOPIC,
                        self._topic_address(pool.bot_wallet),
                        self._topic_address("0x00000000000000000000000000000000000000aa"),
                    ],
                    "data": self._uint_data(571685),
                },
                {
                    "address": pool.token1_address,
                    "topics": [
                        TRANSFER_TOPIC,
                        self._topic_address("0x00000000000000000000000000000000000000bb"),
                        self._topic_address(pool.bot_wallet),
                    ],
                    "data": self._uint_data(123456789),
                },
            ],
        }

        def fake_resume(w3, pool_arg, adapter_arg, position, reserved0, reserved1, allow_swap):
            captured.update({"reserved0": reserved0, "reserved1": reserved1, "allow_swap": allow_swap})
            return {"pool": pool_arg.name, "token_id": position.token_id, "state": "RESUMED"}

        worker._resume_mint_from_reservation = fake_resume
        result = worker._recover_withdrawn_unminted(
            w3=FakeW3(receipt),
            pool=pool,
            adapter=FakeAdapter(balance0=1408306, balance1=999),
            job={
                "old_token_id": 2019344,
                "status": "RECOVERY_REQUIRED",
                "wallet_address": pool.bot_wallet,
                "old_tick_lower": -100,
                "old_tick_upper": 100,
                "reserved_token0_address": pool.token0_address,
                "reserved_token1_address": pool.token1_address,
                "reserved_token0_raw": "1979991",
                "reserved_token1_raw": "10",
                "swap_tx_hash": "0x" + "a" * 64,
            },
        )

        self.assertEqual(result["state"], "RESUMED")
        self.assertEqual(captured, {"reserved0": 1408306, "reserved1": 123456799, "allow_swap": False})
        self.assertEqual(worker.journal.recorded_reservations[0][4:], (1408306, 123456799))
        self.assertEqual(worker.journal.snapshots[0], ("BASE", 2019344, "post_swap", 1408306, 999))

    def test_recovery_existing_swap_accepts_hash_without_0x_prefix(self):
        worker = make_worker()
        pool = make_pool()
        worker.journal = FakeJournal()
        worker.notifier = FakeNotifier()
        captured = {}
        receipt = {
            "status": 1,
            "logs": [
                {
                    "address": pool.token0_address,
                    "topics": [
                        TRANSFER_TOPIC,
                        self._topic_address(pool.bot_wallet),
                        self._topic_address("0x00000000000000000000000000000000000000aa"),
                    ],
                    "data": self._uint_data(571685),
                },
                {
                    "address": pool.token1_address,
                    "topics": [
                        TRANSFER_TOPIC,
                        self._topic_address("0x00000000000000000000000000000000000000bb"),
                        self._topic_address(pool.bot_wallet),
                    ],
                    "data": self._uint_data(123456789),
                },
            ],
        }

        def fake_resume(w3, pool_arg, adapter_arg, position, reserved0, reserved1, allow_swap):
            captured.update({"reserved0": reserved0, "reserved1": reserved1, "allow_swap": allow_swap})
            return {"pool": pool_arg.name, "token_id": position.token_id, "state": "RESUMED"}

        worker._resume_mint_from_reservation = fake_resume
        result = worker._recover_withdrawn_unminted(
            w3=FakeW3(receipt),
            pool=pool,
            adapter=FakeAdapter(balance0=1408306, balance1=999),
            job={
                "old_token_id": 2019344,
                "status": "RECOVERY_REQUIRED",
                "wallet_address": pool.bot_wallet,
                "old_tick_lower": -100,
                "old_tick_upper": 100,
                "reserved_token0_address": pool.token0_address,
                "reserved_token1_address": pool.token1_address,
                "reserved_token0_raw": "1979991",
                "reserved_token1_raw": "10",
                "swap_tx_hash": "a" * 64,
            },
        )

        self.assertEqual(result["state"], "RESUMED")
        self.assertEqual(captured, {"reserved0": 1408306, "reserved1": 123456799, "allow_swap": False})
        self.assertEqual(worker.journal.recorded_reservations[0][4:], (1408306, 123456799))

    def test_swap_receipt_lookup_fallback_uses_backup_rpc(self):
        worker = make_worker()
        pool = make_pool()
        receipt = {"status": 1, "logs": []}
        worker._receipt_rpc_urls = lambda pool_arg: [("backup-test", "https://backup.example")]
        worker._web3_for_rpc = lambda pool_arg, url: FakeW3(receipt)

        result = worker._fetch_receipt_with_rpc_fallback(
            FakeW3(RuntimeError("primary missing receipt")),
            pool,
            token_id=2019344,
            tx_hash="0x" + "a" * 64,
        )

        self.assertIs(result, receipt)

    def test_swap_receipt_lookup_fallback_returns_none_when_all_rpc_fail(self):
        worker = make_worker()
        pool = make_pool()
        worker._receipt_rpc_urls = lambda pool_arg: [("backup-test", "https://backup.example")]
        worker._web3_for_rpc = lambda pool_arg, url: FakeW3(RuntimeError("backup missing receipt"))

        result = worker._fetch_receipt_with_rpc_fallback(
            FakeW3(RuntimeError("primary missing receipt")),
            pool,
            token_id=2019344,
            tx_hash="0x" + "a" * 64,
        )

        self.assertIsNone(result)

    def test_recovery_existing_swap_reconciles_when_primary_receipt_fails_and_backup_succeeds(self):
        worker = make_worker()
        pool = make_pool()
        worker.journal = FakeJournal()
        worker.notifier = FakeNotifier()
        captured = {}
        receipt = {
            "status": 1,
            "logs": [
                {
                    "address": pool.token0_address,
                    "topics": [
                        TRANSFER_TOPIC,
                        self._topic_address(pool.bot_wallet),
                        self._topic_address("0x00000000000000000000000000000000000000aa"),
                    ],
                    "data": self._uint_data(571685),
                },
                {
                    "address": pool.token1_address,
                    "topics": [
                        TRANSFER_TOPIC,
                        self._topic_address("0x00000000000000000000000000000000000000bb"),
                        self._topic_address(pool.bot_wallet),
                    ],
                    "data": self._uint_data(123456789),
                },
            ],
        }
        worker._receipt_rpc_urls = lambda pool_arg: [("backup-test", "https://backup.example")]
        worker._web3_for_rpc = lambda pool_arg, url: FakeW3(receipt)

        def fake_resume(w3, pool_arg, adapter_arg, position, reserved0, reserved1, allow_swap):
            captured.update({"reserved0": reserved0, "reserved1": reserved1, "allow_swap": allow_swap})
            return {"pool": pool_arg.name, "token_id": position.token_id, "state": "RESUMED"}

        worker._resume_mint_from_reservation = fake_resume
        result = worker._recover_withdrawn_unminted(
            w3=FakeW3(RuntimeError("primary missing receipt")),
            pool=pool,
            adapter=FakeAdapter(balance0=1408306, balance1=999),
            job={
                "old_token_id": 2019344,
                "status": "RECOVERY_REQUIRED",
                "wallet_address": pool.bot_wallet,
                "old_tick_lower": -100,
                "old_tick_upper": 100,
                "reserved_token0_address": pool.token0_address,
                "reserved_token1_address": pool.token1_address,
                "reserved_token0_raw": "1979991",
                "reserved_token1_raw": "10",
                "swap_tx_hash": "0x" + "a" * 64,
            },
        )

        self.assertEqual(result["state"], "RESUMED")
        self.assertEqual(captured, {"reserved0": 1408306, "reserved1": 123456799, "allow_swap": False})
        self.assertEqual(worker.journal.recorded_reservations[0][4:], (1408306, 123456799))

    def test_recovery_existing_swap_reverted_retries_from_original_reservation(self):
        worker = make_worker()
        pool = make_pool()
        worker.journal = FakeJournal()
        worker.notifier = FakeNotifier()
        captured = {}
        receipt = {
            "status": 0,
            "blockNumber": 123,
            "gasUsed": 456,
            "logs": [],
        }

        def fake_resume(w3, pool_arg, adapter_arg, position, reserved0, reserved1, allow_swap):
            captured.update({"reserved0": reserved0, "reserved1": reserved1, "allow_swap": allow_swap})
            return {"pool": pool_arg.name, "token_id": position.token_id, "state": "RESUMED"}

        worker._resume_mint_from_reservation = fake_resume
        result = worker._recover_withdrawn_unminted(
            w3=FakeW3(receipt),
            pool=pool,
            adapter=FakeAdapter(balance0=1408306, balance1=999),
            job={
                "old_token_id": 2019344,
                "status": "SWAP_BLOCKED",
                "wallet_address": pool.bot_wallet,
                "old_tick_lower": -100,
                "old_tick_upper": 100,
                "reserved_token0_address": pool.token0_address,
                "reserved_token1_address": pool.token1_address,
                "reserved_token0_raw": "1979991",
                "reserved_token1_raw": "10",
                "swap_tx_hash": "0x" + "a" * 64,
            },
        )

        self.assertEqual(result["state"], "RESUMED")
        self.assertEqual(captured, {"reserved0": 1979991, "reserved1": 10, "allow_swap": True})
        self.assertEqual(worker.journal.recorded_reservations, [])
        self.assertIn("previous swap tx reverted on-chain", worker.journal.statuses[0][6]["error_reason"])

    def test_recovery_unknown_mint_hash_recovers_token_and_stakes(self):
        worker = make_worker()
        pool = make_pool()
        worker.journal = FakeJournal()
        worker.notifier = FakeNotifier()
        worker._notify_discord_pnl_after_delay = lambda *args, **kwargs: None
        adapter = FakeRecoveredMintAdapter(pool)
        worker._lookup_mint_tx_with_rpc_fallback = lambda w3, pool, token_id, tx_hash: {
            "status": "RECEIPT_SUCCESS",
            "receipt": adapter._mint_receipt_from_result(TxResult(tx_hash=tx_hash)),
            "rpc_label": "primary",
        }

        result = worker._recover_withdrawn_unminted(
            w3=None,
            pool=pool,
            adapter=adapter,
            job={
                "old_token_id": 123,
                "status": "RECOVERY_REQUIRED",
                "wallet_address": pool.bot_wallet,
                "old_tick_lower": -100,
                "old_tick_upper": 100,
                "new_tick_lower": -50,
                "new_tick_upper": 50,
                "amount0_desired": "10",
                "amount1_desired": "20",
                "mint_tx_hash": "0x" + "1" * 64,
            },
        )

        self.assertEqual(result["state"], "REMINTED")
        self.assertEqual(result["new_token_id"], 456)
        self.assertEqual(adapter.staked, [456])
        self.assertEqual(worker.journal.statuses[0][2].value, "MINTED_UNSTAKED")
        self.assertEqual(worker.journal.statuses[1][2].value, "REMINTED")

    def test_minted_unstaked_recovery_respects_unstaked_restore_mode(self):
        worker = make_worker()
        pool = make_pool()
        worker.journal = FakeJournal()
        worker.notifier = FakeNotifier()
        worker._notify_discord_pnl_after_delay = lambda *args, **kwargs: None
        adapter = FakeRecoveredMintAdapter(pool)

        result = worker._recover_minted_unstaked(
            pool,
            adapter,
            {
                "old_token_id": 123,
                "new_token_id": 456,
                "wallet_address": pool.bot_wallet,
                "old_tick_lower": -100,
                "old_tick_upper": 100,
                "restore_stake_mode": "UNSTAKED",
            },
        )

        self.assertEqual(result["state"], "REMINTED_UNSTAKED")
        self.assertEqual(adapter.staked, [])
        self.assertEqual(worker.journal.statuses[0][2].value, "REMINTED_UNSTAKED")

    def test_recovery_unknown_mint_hash_not_found_clears_hash_and_retries_reservation(self):
        worker = make_worker()
        pool = make_pool()
        worker.journal = FakeJournal()
        worker.notifier = FakeNotifier()
        worker._lookup_mint_tx_with_rpc_fallback = lambda w3, pool, token_id, tx_hash: {
            "status": "TX_NOT_FOUND",
            "receipt": None,
        }
        retried_jobs = []

        def retry_from_reservation(w3, pool, adapter, job):
            retried_jobs.append(job)
            return {"pool": pool.name, "token_id": job["old_token_id"], "state": "RETRIED"}

        worker._recover_withdrawn_unminted = retry_from_reservation
        result = ConfiguredPoolRebalancer._recover_unknown_mint(
            worker,
            None,
            pool,
            FakeAdapter(0, 0),
            {
                "old_token_id": 123,
                "status": "RECOVERY_REQUIRED",
                "old_tick_lower": -100,
                "old_tick_upper": 100,
                "new_tick_lower": -50,
                "new_tick_upper": 50,
                "amount0_desired": "10",
                "amount1_desired": "20",
                "mint_tx_hash": "0x" + "1" * 64,
            },
            PositionSnapshot(
                token_id=123,
                owner=pool.bot_wallet,
                pool_address=pool.pool_address,
                token0=pool.token0_address,
                token1=pool.token1_address,
                fee=pool.fee,
                tick_lower=-100,
                tick_upper=100,
                liquidity=1,
            ),
            "0x" + "1" * 64,
        )

        self.assertEqual(result["state"], "RETRIED")
        self.assertIsNone(retried_jobs[0]["mint_tx_hash"])
        self.assertEqual(retried_jobs[0]["status"], "WITHDRAWN_UNBURNED")
        self.assertEqual(worker.journal.statuses[0][2], "CLEAR_MINT_TX")

    @staticmethod
    def _topic_address(address: str) -> str:
        return "0x" + address.lower().replace("0x", "").rjust(64, "0")

    @staticmethod
    def _uint_data(value: int) -> str:
        return "0x" + hex(value)[2:].rjust(64, "0")


class RecoveryDiscordTests(unittest.TestCase):
    def test_recovery_notify_is_sent_once_for_same_error(self):
        worker = make_worker(discord_enabled=True)
        pool = make_pool()
        worker.journal = FakeJournal()
        worker.notifier = FakeNotifier()

        worker._notify_recovery_required(pool, 123, "missing reservation ledger")
        worker._notify_recovery_required(pool, 123, "missing reservation ledger")

        self.assertEqual(len(worker.notifier.sent), 1)
        self.assertEqual(worker.journal.notified, [("BASE", 123)])

    def test_recovery_required_message_contains_operator_context(self):
        notifier = DiscordNotifier(WorkerConfig(pools=(make_pool(),)))
        message = notifier.recovery_required_message(
            pool_name="USDC-POP-0.25",
            chain="BASE",
            wallet=WALLET,
            token_id=2013280,
            reason="missing reservation ledger",
        )

        self.assertIn("Recovery Required", message)
        self.assertIn("USDC-POP-0.25", message)
        self.assertIn("2013280", message)
        self.assertIn("missing reservation ledger", message)

    def test_partial_notify_is_sent_once_for_same_key(self):
        worker = make_worker(discord_enabled=True)
        pool = make_pool()
        worker.journal = FakeJournal()
        worker.notifier = FakeNotifier()

        worker._notify_partial_action(
            pool,
            123,
            "swap",
            "SWAP_PENDING",
            "swap receipt timeout",
            "inspect receipt next cycle",
            tx_hash="0x" + "1" * 64,
        )
        worker._notify_partial_action(
            pool,
            123,
            "swap",
            "SWAP_PENDING",
            "swap receipt timeout",
            "inspect receipt next cycle",
            tx_hash="0x" + "1" * 64,
        )

        self.assertEqual(len(worker.notifier.sent), 1)
        self.assertEqual(len(worker.journal.partial_notified), 1)

    def test_partial_notify_sends_again_for_new_status(self):
        worker = make_worker(discord_enabled=True)
        pool = make_pool()
        worker.journal = FakeJournal()
        worker.notifier = FakeNotifier()
        tx_hash = "0x" + "1" * 64

        worker._notify_partial_action(pool, 123, "swap", "SWAP_PENDING", "timeout", "inspect", tx_hash=tx_hash)
        worker._notify_partial_action(pool, 123, "swap", "SWAP_BLOCKED", "reverted", "retry", tx_hash=tx_hash)

        self.assertEqual(len(worker.notifier.sent), 2)
        self.assertEqual(len(worker.journal.partial_notified), 2)

    def test_partial_notify_skips_when_discord_disabled(self):
        worker = make_worker(discord_enabled=False)
        pool = make_pool()
        worker.journal = FakeJournal()
        worker.notifier = FakeNotifier()

        worker._notify_partial_action(pool, 123, "swap", "SWAP_PENDING", "timeout", "inspect")

        self.assertEqual(worker.notifier.sent, [])
        self.assertEqual(worker.journal.partial_notified, [])

    def test_partial_action_message_contains_operator_context(self):
        notifier = DiscordNotifier(WorkerConfig(pools=(make_pool(),)))
        tx_hash = "0x" + "1" * 64
        message = notifier.partial_action_message(
            pool_name="USDC-POP-0.25",
            chain="BASE",
            wallet=WALLET,
            old_token_id=123,
            new_token_id=456,
            action="stake",
            status="MINTED_UNSTAKED",
            tx_hash=tx_hash,
            signed_tx_hash=None,
            reason="stake failed",
            next_action="recovery will retry staking",
        )

        self.assertIn("Transaction Partial", message)
        self.assertIn("USDC-POP-0.25", message)
        self.assertIn("MINTED_UNSTAKED", message)
        self.assertIn(tx_hash, message)
        self.assertIn("recovery will retry staking", message)


if __name__ == "__main__":
    unittest.main()
