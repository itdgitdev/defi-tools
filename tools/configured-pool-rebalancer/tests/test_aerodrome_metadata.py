from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from configured_pool_rebalancer.chain_config import AERODROME_FACTORY_NPM_ADDRESSES
from configured_pool_rebalancer.adapter import AerodromeGaugeAdapter, ZERO_ADDRESS
from configured_pool_rebalancer.models import DexType, PoolConfig


POOL = "0x0000000000000000000000000000000000000001"
WALLET = "0x0000000000000000000000000000000000000002"
TOKEN0 = "0x0000000000000000000000000000000000000003"
TOKEN1 = "0x0000000000000000000000000000000000000004"
NPM = "0x0000000000000000000000000000000000000005"
GAUGE = "0x0000000000000000000000000000000000000006"
OTHER_NPM = "0x0000000000000000000000000000000000000007"
OTHER_GAUGE = "0x0000000000000000000000000000000000000008"


class FakeCall:
    def __init__(self, value=None, error: Exception | None = None):
        self.value = value
        self.error = error

    def call(self, *args, **kwargs):
        if self.error:
            raise self.error
        return self.value


class FakePoolFunctions:
    def __init__(self, *, nft=NPM, gauge=GAUGE):
        self.nft_value = nft
        self.gauge_value = gauge

    def token0(self):
        return FakeCall(TOKEN0)

    def token1(self):
        return FakeCall(TOKEN1)

    def fee(self):
        return FakeCall(27)

    def tickSpacing(self):
        return FakeCall(200)

    def gauge(self):
        if isinstance(self.gauge_value, Exception):
            return FakeCall(error=self.gauge_value)
        return FakeCall(self.gauge_value)

    def nft(self):
        if isinstance(self.nft_value, Exception):
            return FakeCall(error=self.nft_value)
        return FakeCall(self.nft_value)


class FakePoolContract:
    def __init__(self, *, nft=NPM, gauge=GAUGE):
        self.functions = FakePoolFunctions(nft=nft, gauge=gauge)


class FakeTokenFunctions:
    def __init__(self, decimals):
        self.decimals_value = decimals

    def decimals(self):
        return FakeCall(self.decimals_value)


class FakeContract:
    def __init__(self, decimals=18):
        self.functions = FakeTokenFunctions(decimals)


class FakeEth:
    block_number = 123

    def __init__(self, *, missing_code: set[str] | None = None, pool_nft=NPM, pool_gauge=GAUGE):
        self.missing_code = {value.lower() for value in (missing_code or set())}
        self.pool_contract = FakePoolContract(nft=pool_nft, gauge=pool_gauge)

    def contract(self, address, abi):
        if str(address).lower() == POOL.lower():
            return self.pool_contract
        if str(address).lower() == TOKEN1.lower():
            return FakeContract(decimals=6)
        return FakeContract(decimals=18)

    def get_code(self, address, block_identifier=None):
        return b"" if str(address).lower() in self.missing_code else b"\x01"


class FakeMulticall:
    responses: list = []
    calls: list[list] = []

    class Call:
        def __init__(self, address, signature, args=None):
            self.address = address
            self.signature = signature
            self.args = args

    def __init__(self, w3):
        self.items = []
        self.__class__.calls.append(self.items)

    def add(self, item):
        self.items.append(item)

    def call(self, block_identifier=None):
        response = self.__class__.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_pool(**overrides) -> PoolConfig:
    values = {
        "name": "AERO",
        "chain": "BAS",
        "pool_address": POOL,
        "dex_type": DexType.AERODROME_GAUGE,
        "managed_wallets": (WALLET,),
        "bot_wallet": WALLET,
    }
    values.update(overrides)
    return PoolConfig(**values)


def make_adapter(pool: PoolConfig, eth: FakeEth | None = None, *, use_db=False):
    w3 = SimpleNamespace(eth=eth or FakeEth())
    worker_config = SimpleNamespace(use_db_position_cache=use_db)
    executor = SimpleNamespace(worker_config=worker_config)
    return AerodromeGaugeAdapter(w3, pool, executor)


class AerodromeMetadataTests(unittest.TestCase):
    def setUp(self):
        FakeMulticall.responses = []
        FakeMulticall.calls = []

    @patch("configured_pool_rebalancer.adapter.W3Multicall", FakeMulticall)
    def test_resolves_all_metadata_without_configured_contracts(self):
        FakeMulticall.responses = [
            [TOKEN0, TOKEN1, 27, 200, GAUGE, NPM],
            [18, 6],
        ]
        adapter = make_adapter(make_pool())

        with self.assertLogs("configured_pool_rebalancer", level="INFO") as logs:
            resolved = adapter.discover_pool_metadata()

        self.assertEqual(resolved.npm_address.lower(), NPM.lower())
        self.assertEqual(resolved.staking_address.lower(), GAUGE.lower())
        self.assertEqual(resolved.token0_address.lower(), TOKEN0.lower())
        self.assertEqual(resolved.token1_address.lower(), TOKEN1.lower())
        self.assertEqual(resolved.token0_decimals, 18)
        self.assertEqual(resolved.token1_decimals, 6)
        self.assertEqual(resolved.fee, 27)
        self.assertEqual(resolved.tick_spacing, 200)
        self.assertEqual(len(FakeMulticall.calls[0]), 6)
        self.assertIn("npm_source=POOL_NFT", "\n".join(logs.output))
        self.assertIn("gauge_source=POOL_GAUGE", "\n".join(logs.output))

    @patch("configured_pool_rebalancer.adapter.W3Multicall", FakeMulticall)
    def test_config_mismatch_warns_and_onchain_metadata_wins(self):
        FakeMulticall.responses = [
            [TOKEN0, TOKEN1, 27, 200, GAUGE, NPM],
            [18, 6],
        ]
        pool = make_pool(
            token0_address=OTHER_NPM,
            token1_address=OTHER_GAUGE,
            fee=99,
            tick_spacing=100,
            npm_address=OTHER_NPM,
            staking_address=OTHER_GAUGE,
        )
        adapter = make_adapter(pool)

        with self.assertLogs("configured_pool_rebalancer", level="WARNING") as logs:
            resolved = adapter.discover_pool_metadata()

        self.assertEqual(resolved.npm_address.lower(), NPM.lower())
        self.assertEqual(resolved.staking_address.lower(), GAUGE.lower())
        message = "\n".join(logs.output)
        self.assertIn("AERODROME_METADATA_MISMATCH", message)
        self.assertIn("npm_address", message)
        self.assertIn("staking_address", message)

    @patch("configured_pool_rebalancer.adapter.W3Multicall", FakeMulticall)
    def test_zero_gauge_is_valid_no_gauge_state(self):
        FakeMulticall.responses = [
            [TOKEN0, TOKEN1, 27, 200, ZERO_ADDRESS, NPM],
            [18, 6],
        ]
        adapter = make_adapter(make_pool())

        with self.assertLogs("configured_pool_rebalancer", level="INFO") as logs:
            resolved = adapter.discover_pool_metadata()

        self.assertIsNone(resolved.staking_address)
        self.assertIn("gauge_source=NONE", "\n".join(logs.output))

    @patch("configured_pool_rebalancer.adapter.W3Multicall", FakeMulticall)
    def test_multicall_revert_uses_individual_calls_and_db_npm_fallback(self):
        FakeMulticall.responses = [RuntimeError("multicall reverted"), [18, 6]]
        adapter = make_adapter(make_pool(), FakeEth(pool_nft=RuntimeError("nft unavailable")))
        adapter._db_expected_npm = lambda: NPM

        with self.assertLogs("configured_pool_rebalancer", level="INFO") as logs:
            resolved = adapter.discover_pool_metadata()

        self.assertEqual(resolved.npm_address.lower(), NPM.lower())
        self.assertEqual(resolved.staking_address.lower(), GAUGE.lower())
        self.assertIn("npm_source=DB_FACTORY", "\n".join(logs.output))

    @patch("configured_pool_rebalancer.adapter.W3Multicall", FakeMulticall)
    def test_individual_nft_and_gauge_failures_use_config_fallbacks(self):
        FakeMulticall.responses = [RuntimeError("multicall reverted"), [18, 6]]
        eth = FakeEth(
            pool_nft=RuntimeError("nft unavailable"),
            pool_gauge=RuntimeError("gauge unavailable"),
        )
        adapter = make_adapter(make_pool(npm_address=NPM, staking_address=GAUGE), eth)

        with self.assertLogs("configured_pool_rebalancer", level="INFO") as logs:
            resolved = adapter.discover_pool_metadata()

        self.assertEqual(resolved.npm_address.lower(), NPM.lower())
        self.assertEqual(resolved.staking_address.lower(), GAUGE.lower())
        message = "\n".join(logs.output)
        self.assertIn("npm_source=CONFIG_FALLBACK", message)
        self.assertIn("gauge_source=CONFIG_FALLBACK", message)

    @patch("configured_pool_rebalancer.adapter.W3Multicall", FakeMulticall)
    def test_db_mismatch_does_not_override_pool_nft(self):
        FakeMulticall.responses = [
            [TOKEN0, TOKEN1, 27, 200, GAUGE, NPM],
            [18, 6],
        ]
        adapter = make_adapter(make_pool())
        adapter._db_expected_npm = lambda: OTHER_NPM

        with self.assertLogs("configured_pool_rebalancer", level="WARNING") as logs:
            resolved = adapter.discover_pool_metadata()

        self.assertEqual(resolved.npm_address.lower(), NPM.lower())
        self.assertIn("db_npm_address", "\n".join(logs.output))

    @patch("configured_pool_rebalancer.adapter.W3Multicall", FakeMulticall)
    def test_missing_npm_bytecode_stops_before_discovery(self):
        FakeMulticall.responses = [
            [TOKEN0, TOKEN1, 27, 200, GAUGE, NPM],
            [18, 6],
        ]
        adapter = make_adapter(make_pool(), FakeEth(missing_code={NPM}))

        with self.assertRaisesRegex(RuntimeError, "NPM_METADATA_UNRESOLVED"):
            adapter.discover_pool_metadata()

    def test_db_factory_mapping_is_used_only_when_db_cache_enabled(self):
        factory, expected_npm = next(iter(AERODROME_FACTORY_NPM_ADDRESSES["BAS"].items()))
        executed = []
        closed = []

        class Cursor:
            def execute(self, query, params):
                executed.append(params)

            def fetchone(self):
                return {"factory_address": factory}

            def close(self):
                closed.append("cursor")

        class Connection:
            def cursor(self, dictionary=False):
                self.assert_dictionary = dictionary
                return Cursor()

            def close(self):
                closed.append("connection")

        disabled = make_adapter(make_pool(), use_db=False)
        self.assertIsNone(disabled._db_expected_npm())
        self.assertEqual(executed, [])

        enabled = make_adapter(make_pool(), use_db=True)
        with patch("configured_pool_rebalancer.database.get_connection", return_value=Connection()):
            resolved = enabled._db_expected_npm()

        self.assertEqual(resolved.lower(), expected_npm.lower())
        self.assertEqual(executed, [("BAS", POOL)])
        self.assertEqual(closed, ["cursor", "connection"])


if __name__ == "__main__":
    unittest.main()
