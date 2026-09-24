from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from web3 import Web3

from configured_pool_rebalancer.adapter import PancakeV3MasterChefAdapter
from configured_pool_rebalancer.models import DexType, GasPolicy, PoolConfig, TxResult


TOKEN0 = "0x0000000000000000000000000000000000000003"
TOKEN1 = "0x0000000000000000000000000000000000000004"
WALLET = "0x0000000000000000000000000000000000000002"


class FakeExecutor:
    dry_run = False


class FakeTxHash(bytes):
    def hex(self):
        return "0x" + super().hex()


class RawSwapExecutor:
    dry_run = False

    def __init__(self):
        self.sign_calls = []

    def _next_nonce(self):
        return 9

    def ensure_wallet_guard_clear(self):
        return None

    def require_write_w3(self):
        return self.write_w3

    def _validate_protected_transaction(self, tx):
        return None

    def reserve_wallet_guard(self, *args):
        return None

    def record_guard_broadcast(self, *args):
        return None

    def record_guard_error(self, *args):
        return None

    def clear_wallet_guard(self, *args):
        return True

    def write_rpc_label(self):
        return "protected"

    def gas_policy(self):
        return GasPolicy(max_fee_gwei=0.1)

    def sign_transaction(self, wallet, tx):
        self.sign_calls.append((wallet, tx))
        return SimpleNamespace(raw_transaction=b"signed-swap")


class FakeSwapper:
    quote = None
    routes = None

    def __init__(self, chain_name, rpc_url):
        self.chain_name = chain_name
        self.rpc_url = rpc_url

    def get_swap_routes(self, *args):
        if self.routes is not None:
            return self.routes
        return [self.quote] if self.quote else []

    def get_best_swap_route(self, *args):
        return self.quote


def make_pool(price_impact_cap=1.0, min_swap_output_usd=0.0):
    return PoolConfig(
        name="TEST",
        chain="BAS",
        pool_address="0x0000000000000000000000000000000000000001",
        dex_type=DexType.PANCAKE_V3_MASTERCHEF,
        managed_wallets=(WALLET,),
        bot_wallet=WALLET,
        token0_address=TOKEN0,
        token1_address=TOKEN1,
        token0_decimals=6,
        token1_decimals=18,
        fee=2500,
        max_swap_price_impact_pct=price_impact_cap,
        min_swap_output_usd=min_swap_output_usd,
    )


def make_adapter(pool):
    adapter = PancakeV3MasterChefAdapter.__new__(PancakeV3MasterChefAdapter)
    adapter.pool = pool
    adapter.executor = FakeExecutor()
    adapter.approvals = []
    adapter.sent_payloads = []
    adapter.approve_if_needed = lambda token, spender, amount: adapter.approvals.append((token, spender, amount))
    adapter._simulate_swap = lambda tx, metadata: (True, None)

    def send_raw_swap(tx, metadata=None):
        adapter.sent_payloads.append((tx, metadata))
        return TxResult(tx_hash="0x" + "1" * 64, metadata=metadata or {})

    adapter._send_raw_swap = send_raw_swap
    return adapter


class AdapterSwapperIntegrationTests(unittest.TestCase):
    def setUp(self):
        FakeSwapper.quote = None
        FakeSwapper.routes = None

    def test_swap_uses_internal_swapper_and_preserves_metadata(self):
        pool = make_pool()
        adapter = make_adapter(pool)
        FakeSwapper.quote = {
            "provider": "0x",
            "to": "0x0000000000000000000000000000000000000012",
            "data": "0x1234",
            "value": "0",
            "allowanceTarget": "0x0000000000000000000000000000000000000013",
            "buyAmount": "1500",
            "price_impact": 0.2,
        }

        with patch("configured_pool_rebalancer.swapper.V3Swapper", FakeSwapper):
            result = adapter.swap(TOKEN0, TOKEN1, 1000)

        self.assertIsNotNone(result)
        self.assertEqual(adapter.approvals, [(TOKEN0, "0x0000000000000000000000000000000000000013", 1000)])
        tx, metadata = adapter.sent_payloads[0]
        self.assertEqual(tx["to"], "0x0000000000000000000000000000000000000012")
        self.assertEqual(metadata["token_in"], TOKEN0)
        self.assertEqual(metadata["token_out"], TOKEN1)
        self.assertEqual(metadata["amount_in"], "1000")
        self.assertEqual(metadata["quote_buy_amount"], "1500")
        self.assertEqual(metadata["price_impact"], 0.2)
        self.assertEqual(metadata["provider"], "0x")

    def test_swap_returns_none_when_price_impact_exceeds_cap(self):
        pool = make_pool(price_impact_cap=0.1)
        adapter = make_adapter(pool)
        FakeSwapper.quote = {
            "provider": "0x",
            "to": "0x0000000000000000000000000000000000000012",
            "data": "0x1234",
            "value": "0",
            "buyAmount": "1500",
            "price_impact": 0.2,
        }

        with patch("configured_pool_rebalancer.swapper.V3Swapper", FakeSwapper):
            result = adapter.swap(TOKEN0, TOKEN1, 1000)

        self.assertIsNone(result)
        self.assertEqual(adapter.sent_payloads, [])

    def test_swap_skips_simulation_failed_route_and_broadcasts_next_route(self):
        pool = make_pool()
        adapter = make_adapter(pool)
        FakeSwapper.routes = [
            {
                "provider": "0x",
                "route_display": "bad route",
                "to": "0x0000000000000000000000000000000000000012",
                "data": "0x1234",
                "value": "0",
                "buyAmount": "2000",
                "price_impact": 0.2,
            },
            {
                "provider": "OKX",
                "route_display": "good route",
                "to": "0x0000000000000000000000000000000000000015",
                "data": "0xbeef",
                "value": "0",
                "buyAmount": "1500",
                "price_impact": 0.2,
            },
        ]
        attempts = []

        def simulate(tx, metadata):
            attempts.append(metadata["provider"])
            return (metadata["provider"] == "OKX", "execution reverted")

        adapter._simulate_swap = simulate

        with patch("configured_pool_rebalancer.swapper.V3Swapper", FakeSwapper):
            result = adapter.swap(TOKEN0, TOKEN1, 1000)

        self.assertIsNotNone(result)
        self.assertEqual(attempts, ["0x", "OKX"])
        self.assertEqual(len(adapter.sent_payloads), 1)
        tx, metadata = adapter.sent_payloads[0]
        self.assertEqual(tx["to"], "0x0000000000000000000000000000000000000015")
        self.assertEqual(metadata["provider"], "OKX")
        self.assertEqual(metadata["route_display"], "good route")

    def test_swap_returns_failed_when_all_routes_fail_simulation(self):
        pool = make_pool()
        adapter = make_adapter(pool)
        FakeSwapper.routes = [
            {
                "provider": "0x",
                "to": "0x0000000000000000000000000000000000000012",
                "data": "0x1234",
                "value": "0",
                "buyAmount": "2000",
                "price_impact": 0.2,
            },
            {
                "provider": "OKX",
                "to": "0x0000000000000000000000000000000000000015",
                "data": "0xbeef",
                "value": "0",
                "buyAmount": "1500",
                "price_impact": 0.2,
            },
        ]
        adapter._simulate_swap = lambda tx, metadata: (False, "execution reverted")

        with patch("configured_pool_rebalancer.swapper.V3Swapper", FakeSwapper):
            result = adapter.swap(TOKEN0, TOKEN1, 1000)

        self.assertIsNotNone(result)
        self.assertEqual(result.status, "FAILED")
        self.assertEqual(result.tx_hash, "failed:swap-simulation")
        self.assertEqual(result.metadata["error"], "all swap routes failed simulation")
        self.assertEqual(adapter.sent_payloads, [])

    def test_swap_revert_classification(self):
        self.assertEqual(
            PancakeV3MasterChefAdapter._classify_swap_revert(980, 1000),
            "OUT_OF_GAS_LIKELY",
        )
        self.assertEqual(
            PancakeV3MasterChefAdapter._classify_swap_revert(700, 1000),
            "ROUTE_OR_SLIPPAGE_REVERT",
        )

    def test_send_raw_swap_uses_executor_signer(self):
        pool = make_pool()
        adapter = PancakeV3MasterChefAdapter.__new__(PancakeV3MasterChefAdapter)
        adapter.pool = pool
        adapter.executor = RawSwapExecutor()
        tx_hash = FakeTxHash(bytes.fromhex("34" * 32))
        w3 = SimpleNamespace()
        w3.eth = SimpleNamespace()
        w3.eth.estimate_gas = lambda tx: 21000
        w3.eth.send_raw_transaction = lambda raw: tx_hash
        w3.eth.wait_for_transaction_receipt = lambda got_hash, timeout: {
            "status": 1,
            "transactionHash": got_hash,
            "blockNumber": 123,
            "gasUsed": 456,
            "effectiveGasPrice": Web3.to_wei(0.006, "gwei"),
        }
        adapter.w3 = w3
        adapter.executor.write_w3 = w3

        with (
            patch(
                "configured_pool_rebalancer.evm.get_gas_params",
                return_value={"maxFeePerGas": Web3.to_wei(0.01, "gwei"), "maxPriorityFeePerGas": 1},
            ),
            patch("configured_pool_rebalancer.evm.validate_gas_cap"),
            patch("configured_pool_rebalancer.evm.get_chain_id", return_value=8453),
            patch("configured_pool_rebalancer.chain_config.RPC_URLS_2", {"BAS": None}),
            patch("configured_pool_rebalancer.chain_config.RPC_BACKUP_LIST", {"BAS": []}),
        ):
            result = adapter._send_raw_swap(
                {
                    "to": "0x0000000000000000000000000000000000000012",
                    "data": "0x1234",
                    "value": 0,
                },
                metadata={"token_in": TOKEN0, "token_out": TOKEN1, "amount_in": "1000"},
            )

        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(result.tx_hash, tx_hash.hex())
        self.assertEqual(len(adapter.executor.sign_calls), 1)
        signed_wallet, signed_tx = adapter.executor.sign_calls[0]
        self.assertEqual(signed_wallet, WALLET)
        self.assertEqual(signed_tx["nonce"], 9)


if __name__ == "__main__":
    unittest.main()
