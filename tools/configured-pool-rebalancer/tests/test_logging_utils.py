from __future__ import annotations

import unittest

from configured_pool_rebalancer.logging_utils import format_log_block


class LoggingUtilsTests(unittest.TestCase):
    def test_format_log_block_uses_header_context_and_body_fields(self):
        message = format_log_block(
            "swap reverted",
            context={"pool": "USDC-POP-0.25", "chain": "BAS", "old_token_id": 2021200},
            fields={"stage": "swap_receipt", "status": "SWAP_BLOCKED", "gas_used": 827710},
        )

        self.assertTrue(message.startswith("\n=== SWAP REVERTED | pool=USDC-POP-0.25 | chain=BAS | old_token_id=2021200 ==="))
        self.assertIn("\n- stage: swap_receipt", message)
        self.assertIn("\n- status: SWAP_BLOCKED", message)
        self.assertIn("\n- gas_used: 827710", message)

    def test_format_log_block_handles_none_consistently(self):
        message = format_log_block(
            "recovery start",
            context={"pool": "TEST", "chain": None, "old_token_id": 1},
            fields={"new_token_id": None, "next_action": "inspect"},
        )

        self.assertIn("pool=TEST", message)
        self.assertNotIn("chain=", message)
        self.assertIn("new_token_id: null", message)

    def test_format_log_block_filters_sensitive_keys(self):
        message = format_log_block(
            "tx",
            context={
                "pool": "TEST",
                "private_key_env": "SECRET",
                "private_key_prefix": "PREFIX_SEGMENT",
            },
            fields={
                "tx_hash": "0x123",
                "raw_transaction": "0xdeadbeef",
                "discord_webhook": "https://example.invalid/secret",
                "private_key_suffix": "SUFFIX_SEGMENT",
            },
        )

        self.assertIn("tx_hash: 0x123", message)
        self.assertNotIn("SECRET", message)
        self.assertNotIn("PREFIX_SEGMENT", message)
        self.assertNotIn("SUFFIX_SEGMENT", message)
        self.assertNotIn("0xdeadbeef", message)
        self.assertNotIn("webhook", message.lower())


if __name__ == "__main__":
    unittest.main()
