from __future__ import annotations

import argparse
from datetime import date, datetime
import json
import sys

from .tx_guard import WalletTxGuard


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Inspect or manually clear configured rebalancer wallet transaction guards"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list")
    list_parser.add_argument("--chain")
    list_parser.add_argument("--wallet")

    show_parser = subparsers.add_parser("show")
    show_parser.add_argument("--chain", required=True)
    show_parser.add_argument("--wallet", required=True)

    clear_parser = subparsers.add_parser("clear")
    clear_parser.add_argument("--chain", required=True)
    clear_parser.add_argument("--wallet", required=True)
    clear_parser.add_argument("--signed-hash", required=True)
    clear_parser.add_argument("--reason", required=True)
    clear_parser.add_argument("--yes", action="store_true")
    return parser.parse_args(argv)


def main(
    argv: list[str] | None = None,
    *,
    guard: WalletTxGuard | None = None,
    input_fn=input,
) -> int:
    args = parse_args(argv)
    tx_guard = guard or WalletTxGuard()
    if args.command == "list":
        print(_json(tx_guard.list_active(args.chain, args.wallet)))
        return 0

    active = tx_guard.get_active(args.chain, args.wallet)
    if args.command == "show":
        print(_json(active))
        return 0 if active else 1

    reason = str(args.reason or "").strip()
    if not reason:
        raise ValueError("--reason must not be empty")
    if active is None:
        print(_json({"status": "NOT_FOUND"}))
        return 1
    expected_hash = WalletTxGuard._hash(args.signed_hash)
    if str(active["signed_tx_hash"]).lower() != expected_hash:
        print(
            _json(
                {
                    "status": "HASH_MISMATCH",
                    "active_guard": active,
                }
            )
        )
        return 1

    warning = {
        "status": "CONFIRM_CLEAR",
        "guard": active,
        "reason": reason,
        "warning": (
            "Clearing this guard does not update business journals, retry, "
            "or broadcast any transaction."
        ),
    }
    print(_json(warning))
    if not args.yes:
        confirmation = input_fn("Type CLEAR to remove this wallet transaction guard: ")
        if str(confirmation).strip() != "CLEAR":
            print(_json({"status": "CANCELLED"}))
            return 1

    deleted = tx_guard.clear(args.chain, args.wallet, expected_hash)
    payload = {
        "status": "CLEARED" if deleted else "NOT_CLEARED",
        "chain": active["chain"],
        "wallet": active["wallet_address"],
        "nonce": active["nonce"],
        "signed_tx_hash": active["signed_tx_hash"],
        "action": active["action"],
        "pool_name": active.get("pool_name"),
        "reason": reason,
    }
    print(_json(payload))
    return 0 if deleted else 1


def _json(value) -> str:
    return json.dumps(value, indent=2, sort_keys=True, default=_json_default)


def _json_default(value):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
