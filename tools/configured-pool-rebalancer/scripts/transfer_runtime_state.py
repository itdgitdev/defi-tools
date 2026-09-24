"""Read-only cutover check by default; copy cache only after old worker stops."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import mysql.connector

from configured_pool_rebalancer.database import get_connection
from configured_pool_rebalancer.paths import CACHE_DIR, LEGACY_CACHE_DIR, LOCAL_CONFIG
from configured_pool_rebalancer.settings import load_worker_config


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect or transfer rebalancer runtime cache")
    parser.add_argument("--source-cache", type=Path, required=True)
    parser.add_argument("--source-legacy-cache", type=Path)
    parser.add_argument("--config", type=Path, default=LOCAL_CONFIG)
    parser.add_argument("--copy", action="store_true")
    parser.add_argument("--confirm-worker-stopped", action="store_true")
    args = parser.parse_args()
    if args.copy and not args.confirm_worker_stopped:
        parser.error("--copy requires --confirm-worker-stopped")
    if not args.source_cache.is_dir():
        parser.error(f"source cache directory missing: {args.source_cache}")

    config = load_worker_config(args.config, dry_run=True)
    candidates: list[tuple[Path, Path]] = []
    seen: set[tuple[str, str]] = set()
    for pool in config.pools:
        key = (pool.chain.upper(), pool.pool_address.lower())
        if key in seen:
            continue
        seen.add(key)
        filename = f"{pool.chain}_{pool.pool_address.lower()}.json"
        for subdir in (Path(), Path("auto_compound")):
            source = args.source_cache / subdir / filename
            if source.is_file():
                candidates.append((source, CACHE_DIR / subdir / filename))
            else:
                print(f"[WARN] Cache absent: {subdir / filename}")
        print(f"Pool checked: {pool.name} {pool.chain} {pool.pool_address}")
    notification_cache = args.source_cache / "inactive_farm_notifications.json"
    if notification_cache.is_file():
        candidates.append((notification_cache, CACHE_DIR / notification_cache.name))
    if config.use_legacy_position_cache:
        if args.source_legacy_cache is None:
            parser.error("config enables legacy cache; provide --source-legacy-cache")
        for chain in sorted({pool.chain for pool in config.pools}):
            source = args.source_legacy_cache / f"positions_cache_{chain}.json"
            if source.is_file():
                candidates.append((source, LEGACY_CACHE_DIR / source.name))
            else:
                print(f"[WARN] Legacy cache absent: {source.name}")

    for source, target in candidates:
        json.loads(source.read_text(encoding="utf-8"))
        source_stat = (source.stat().st_size, source.stat().st_mtime_ns)
        if target.is_file():
            if digest(source) != digest(target):
                raise RuntimeError(f"destination already differs; refusing overwrite: {target}")
            print(f"[SKIP] Already copied: {target.name}")
            continue
        if args.copy:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            if source_stat != (source.stat().st_size, source.stat().st_mtime_ns):
                target.unlink(missing_ok=True)
                raise RuntimeError(f"source changed during copy: {source}")
            if digest(source) != digest(target):
                target.unlink(missing_ok=True)
                raise RuntimeError(f"copy hash mismatch: {source}")
            print(f"[COPIED] {target.name}")
        else:
            print(f"[READY] {source.name}")

    connection = get_connection()
    try:
        cursor = connection.cursor()
        try:
            for table in ("configured_rebalance_jobs", "configured_compound_jobs"):
                for chain, pool_address in sorted(seen):
                    try:
                        cursor.execute(
                            f"SELECT status, COUNT(*) FROM {table} WHERE chain=%s AND LOWER(pool_address)=LOWER(%s) GROUP BY status",
                            (chain, pool_address),
                        )
                        for status, count in cursor.fetchall():
                            print(f"[DB] {table} {chain} {pool_address}: {status}={count}")
                    except mysql.connector.Error as exc:
                        print(f"[WARN] Cannot inspect {table} for {chain}: {type(exc).__name__}")
        finally:
            cursor.close()
    finally:
        connection.close()
    print(f"Cache files checked: {len(candidates)}; copied: {args.copy}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
