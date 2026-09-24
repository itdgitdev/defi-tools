from __future__ import annotations

import unittest

from eth_account import Account
from web3 import Web3

from configured_pool_rebalancer.signer import RuntimeSigner


PRIVATE_KEY = "0x" + "1" * 64
WALLET = Web3.to_checksum_address(Account.from_key(PRIVATE_KEY).address)
OTHER_WALLET = Web3.to_checksum_address(Account.from_key("0x" + "2" * 64).address)


class RuntimeSignerTests(unittest.TestCase):
    def test_valid_key_matching_wallet_creates_signer(self):
        signer = RuntimeSigner({WALLET: PRIVATE_KEY})

        self.assertIn(WALLET, repr(signer))
        self.assertNotIn(PRIVATE_KEY, repr(signer))

    def test_key_mismatch_raises(self):
        with self.assertRaisesRegex(ValueError, "does not match"):
            RuntimeSigner({OTHER_WALLET: PRIVATE_KEY})

    def test_empty_key_raises(self):
        with self.assertRaisesRegex(ValueError, "empty private key"):
            RuntimeSigner({WALLET: ""})

    def test_invalid_key_raises(self):
        with self.assertRaisesRegex(ValueError, "invalid private key"):
            RuntimeSigner({WALLET: "not-a-private-key"})

    def test_sign_transaction_uses_key_for_wallet(self):
        signer = RuntimeSigner({WALLET: PRIVATE_KEY})
        signed = signer.sign_transaction(
            WALLET,
            {
                "chainId": 8453,
                "nonce": 0,
                "gasPrice": 1,
                "gas": 21000,
                "to": "0x0000000000000000000000000000000000000001",
                "value": 0,
                "data": b"",
            },
        )

        self.assertTrue(signed.raw_transaction)

    def test_missing_wallet_raises(self):
        signer = RuntimeSigner({WALLET: PRIVATE_KEY})

        with self.assertRaisesRegex(RuntimeError, "missing runtime private key"):
            signer.sign_transaction(OTHER_WALLET, {})


if __name__ == "__main__":
    unittest.main()

