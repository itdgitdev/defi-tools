from __future__ import annotations

import unittest
from types import SimpleNamespace

from web3 import Web3

from configured_pool_rebalancer.auto_compound.abi import COLLECT_TOPIC, INCREASE_LIQUIDITY_TOPIC
from configured_pool_rebalancer.auto_compound.models import CompoundJobState
from configured_pool_rebalancer.auto_compound.worker import ConfiguredPoolCompounder
from configured_pool_rebalancer.models import DexType, PoolConfig


def words(*values: int) -> str:
    return "0x" + "".join(value.to_bytes(32, "big").hex() for value in values)


class ReceiptRecoveryTests(unittest.TestCase):
    def test_collect_amounts_decode_from_receipt(self):
        receipt = {"logs": [{"topics": [COLLECT_TOPIC], "data": words(111, 222)}]}
        self.assertEqual(ConfiguredPoolCompounder._event_amounts(receipt, "COLLECT"), (111, 222))

    def test_increase_amounts_decode_from_receipt(self):
        receipt = {"logs": [{"topics": [INCREASE_LIQUIDITY_TOPIC], "data": words(333, 111, 222)}]}
        self.assertEqual(ConfiguredPoolCompounder._event_amounts(receipt, "INCREASE"), (333, 111, 222))

    def test_swap_movement_only_counts_job_wallet_and_tokens(self):
        wallet = "0x0000000000000000000000000000000000000002"
        router = "0x0000000000000000000000000000000000000009"
        token_in = "0x0000000000000000000000000000000000000010"
        token_out = "0x0000000000000000000000000000000000000020"
        topic = Web3.to_hex(Web3.keccak(text="Transfer(address,address,uint256)"))
        address_topic = lambda value: "0x" + value[2:].rjust(64, "0")
        receipt = {
            "logs": [
                {"address": token_in, "topics": [topic, address_topic(wallet), address_topic(router)], "data": words(100)},
                {"address": token_out, "topics": [topic, address_topic(router), address_topic(wallet)], "data": words(90)},
            ]
        }
        movement = ConfiguredPoolCompounder._swap_movement(
            {"wallet_address": wallet, "swap_token_in": token_in, "swap_token_out": token_out}, receipt
        )
        self.assertEqual(movement, (100, 90))

    def test_resume_job_stops_when_runtime_npm_changed(self):
        runtime_npm = "0x0000000000000000000000000000000000000011"
        job_npm = "0x0000000000000000000000000000000000000012"
        updates = []
        compounder = ConfiguredPoolCompounder.__new__(ConfiguredPoolCompounder)
        compounder.journal = SimpleNamespace(update=lambda job_id, **values: updates.append((job_id, values)))
        pool = PoolConfig(
            name="AERO",
            chain="BAS",
            pool_address="0x0000000000000000000000000000000000000001",
            dex_type=DexType.AERODROME_GAUGE,
            managed_wallets=("0x0000000000000000000000000000000000000002",),
            bot_wallet="0x0000000000000000000000000000000000000002",
            npm_address=runtime_npm,
        )
        job = {
            "id": 7,
            "token_id": 99,
            "status": CompoundJobState.COLLECTED.value,
            "npm_address": job_npm,
            "pending_action": None,
        }

        result = compounder._resume_job(SimpleNamespace(), pool, SimpleNamespace(), job)

        self.assertEqual(result["state"], CompoundJobState.RECOVERY_REQUIRED.value)
        self.assertEqual(result["reason"], "NPM_ADDRESS_CHANGED")
        self.assertEqual(updates[0][1]["status"], CompoundJobState.RECOVERY_REQUIRED.value)


if __name__ == "__main__":
    unittest.main()
