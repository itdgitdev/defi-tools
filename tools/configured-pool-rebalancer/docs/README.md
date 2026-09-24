# Configured Pool Rebalancer

Standalone worker for rebalancing configured users' own V3 LP positions across one or more configured pools.

It does not copy competitor ranges and does not modify the existing `parasite_bot` engines.

## Behavior

- Scans staked V3 NFT positions for configured pools.
- Filters positions by `managed_wallets`.
- Bootstraps tokenIds from the legacy `latest_farms/positions_cache/positions_cache_{CHAIN}.json` cache when enabled, then verifies every candidate on-chain.
- Skips in-range positions.
- For out-of-range positions, computes a new range, withdraws/collects, optionally swaps only recovered tokens, mints a new position, stakes it, then burns the old empty NFT when safe.
- By default the new range keeps the old width and recenters around current tick; when `rebalance_range.mode` is `price_percent`, the range is built around current price using lower/upper percentages.
- Defaults to dry-run. Use `--execute` only after reviewing the printed plan.

## Run

The project-root `run_configured_rebalancer.ps1` runs the local
`my_rebalance_config.json` with `--migrate --execute` for one cycle and appends
output to `logs/configured_rebalancer.log`. It resolves paths from `$PSScriptRoot`
and uses `.venv\Scripts\python.exe`; live runs require an interactive terminal.
The root `.env` is shared with Binance Monitor, while each worker keeps its own JSON
config. See the root `.env.example` for a minimal setup; merge entries into an
existing `.env` rather than overwriting it.

For a dry-run, use the CLI without `--execute`:

```powershell
python -m latest_farms.configured_pool_rebalancer.cli --config latest_farms/configured_pool_rebalancer/sample_config.json
```

## Config v2

The worker supports both the original per-pool config shape and the easier v2 shape used by `sample_config.json`.

In v2, shared wallet and pool settings are defined once:

```json
{
  "version": 2,
  "wallets": {
    "main": {
      "bot_wallet": "0x...",
      "private_key_prefix_env": "CONFIGURED_REBALANCER_MAIN_PRIVATE_KEY_PREFIX"
    }
  },
  "pool_defaults": {
    "dex_type": "pancake_v3_masterchef",
    "wallet": "main",
    "slippage_bps": 10,
    "max_swap_price_impact_pct": 0.5,
    "execute_burn": false
  },
  "pools": [
    {
      "name": "USDT-GENIUS",
      "chain": "BNB",
      "pool_address": "0x...",
      "pid": 554
    }
  ]
}
```

Each pool is expanded from the selected wallet alias, then `pool_defaults`, then the pool's own overrides. If `managed_wallets` is omitted, it defaults to `[bot_wallet]`, which is the normal single-signer setup. Use explicit `managed_wallets` only when the position owner list differs from the signer wallet.

## Local position bootstrap

For local runs, you do not need the server-maintained `latest_farms/positions_cache` file if you configure a bootstrap source.

The worker can discover staked positions in three ways:

- `seed_token_ids`: manually list known NFT tokenIds for a quick canary run.
- `bootstrap_start_block`: sweep MasterChef `Deposit`/`Withdraw` logs from this block on the first local run, then keep syncing incrementally from the module cache.
- `auto_bootstrap_start_block`: enabled by default; if no manual block is configured, the worker tries to find the V3 Factory `PoolCreated` block and uses it as the first sweep block.
- Legacy cache: copy `latest_farms/positions_cache/positions_cache_{CHAIN}.json` from a server and keep `use_legacy_position_cache=true`.

For a self-contained local config, prefer:

```json
{
  "use_legacy_position_cache": false,
  "pools": [
    {
      "name": "USDT-GENIUS",
      "chain": "BNB",
      "pool_address": "0x...",
      "pid": 554,
      "bootstrap_start_block": null,
      "auto_bootstrap_start_block": true
    }
  ]
}
```

If there is no module cache, no legacy cache, no `seed_token_ids`, no manual block, and automatic pool-created-block lookup fails, the first run intentionally skips historical logs and may not find old positions.

Create journal tables and run live:

```powershell
python -m latest_farms.configured_pool_rebalancer.cli --config path\to\config.json --migrate --execute
```

Live transactions are broadcast through a protected write RPC; the existing Infura/public RPC remains read-only.
BNB defaults to PancakeSwap MEV Guard and Ethereum defaults to Flashbots Protect Fast. Base and Arbitrum require
dedicated Alchemy write endpoints in the project-root `.env` file:

```dotenv
CONFIGURED_REBALANCER_WRITE_RPC_BAS=https://base-mainnet.g.alchemy.com/v2/<DEDICATED_KEY>
CONFIGURED_REBALANCER_WRITE_RPC_ARB=https://arb-mainnet.g.alchemy.com/v2/<DEDICATED_KEY>
```

The optional overrides are `CONFIGURED_REBALANCER_WRITE_RPC_BNB` and
`CONFIGURED_REBALANCER_WRITE_RPC_ETH`. A live run stops before signing if the protected endpoint is missing,
unavailable, or connected to the wrong chain. It never falls back to a public RPC for transaction broadcast.

Live runs reconstruct each distinct `bot_wallet` private key from two hexadecimal segments:

- A user-selected prefix of 0 to 63 characters is read from the environment variable named by
  `private_key_prefix_env`.
- The CLI derives how many of the 64 characters remain and requests that suffix through a hidden terminal prompt.

The 64-character key does not include the optional `0x` prefix. For example, configure a wallet with:

```json
{
  "bot_wallet": "0x...",
  "private_key_prefix_env": "CONFIGURED_REBALANCER_MAIN_PRIVATE_KEY_PREFIX"
}
```

Then store the desired prefix in the project-root `.env` file. For example, a 44-character prefix makes the CLI
request the remaining 20 characters:

```dotenv
CONFIGURED_REBALANCER_MAIN_PRIVATE_KEY_PREFIX=<44_HEX_CHARACTERS>
```

The CLI loads `.env` without overriding variables already present in the process environment. It validates every prefix
before prompting, then verifies that the reconstructed key matches `bot_wallet`. A blank prefix requests all 64
characters at the terminal. Prefixes of 54 and 44 characters request suffixes of 10 and 20 characters respectively.
Invalid input can be corrected through at most three attempts. The prefix, suffix, and reconstructed key are never
logged; the reconstructed key is kept only in process memory. Do not use this split-key arrangement for a primary wallet
or a wallet holding material funds.

Run an interactive live session every `interval_seconds` seconds (30 minutes by default):

```powershell
python -m latest_farms.configured_pool_rebalancer.cli --config path\to\config.json --migrate --execute --loop
```

Use Task Scheduler only for dry-run/report jobs. Live execution requires an interactive terminal so the operator can
enter the remaining private-key suffix.

Generate a PnL report only:

```powershell
python -m latest_farms.configured_pool_rebalancer.cli --config path\to\config.json --pnl-report
```

The PnL report reads existing data from `wallet_nft_position`, `wallet_nft_summary`, and `configured_rebalance_jobs`. It writes:

- `latest_farms/logs/configured_rebalancer_pnl.json`
- `latest_farms/logs/configured_rebalancer_pnl.csv`

For net PnL after gas in USD, the report fetches the native token market price dynamically. Lookup order is PancakeSwap price API, DexScreener, then CoinGecko.

Optional Discord PnL notification after live rebalance:

```json
{
  "discord": {
    "enabled": true,
    "webhook_url_env": "CONFIGURED_REBALANCER_DISCORD_WEBHOOK",
    "pnl_delay_seconds": 90,
    "notify_pending_if_snapshot_missing": false
  }
}
```

Set the webhook URL in PowerShell before running live:

```powershell
$env:CONFIGURED_REBALANCER_DISCORD_WEBHOOK="https://discord.com/api/webhooks/..."
```

The worker waits `pnl_delay_seconds` after remint/stake, then runs the in-memory PnL reporter and sends the matching record to Discord. If the new NFT snapshot is still missing, it retries the PnL notification on later worker cycles. Pending notifications are disabled by default.

Optional chain-specific gas policy:

```json
{
  "gas_policy": {
    "BNB": {
      "mode": "fixed",
      "gas_price_gwei": 0.05,
      "max_fee_gwei": 0.08
    },
    "BAS": {
      "mode": "eip1559",
      "base_fee_multiplier": 1.2,
      "priority_fee_cap_gwei": 0.002,
      "swap_priority_fee_cap_gwei": 0.005,
      "max_fee_gwei": 0.05
    }
  }
}
```

If omitted, the worker defaults to a low fixed BNB gas policy and a capped dynamic EIP-1559 Base policy. Other chains keep the generic EIP-1559 behavior.

You can optionally configure a fallback price for local/offline runs:

```json
{
  "pnl": {
    "native_prices_usd": {
      "BNB": 590.0
    }
  }
}
```

Optional price-percent rebalance range per pool:

```json
{
  "rebalance_range": {
    "mode": "price_percent",
    "lower_percent": -9.0,
    "upper_percent": 20.0
  }
}
```

This means the new range is approximately current price minus 9% to current price plus 20%. If omitted, the worker tries to infer the original percent band from the module journal or the first `wallet_nft_position` snapshot, then falls back to the old center-width behavior.

Configured percentages are treated as the target strategy. After minting, the journal stores the actual lower/upper percentages derived from the aligned minted ticks, so future fallback behavior reflects the real on-chain range.

## Fee auto-compounding

Auto-compounding is disabled by default and is configured per pool (or through `pool_defaults`):

```json
{
  "auto_compound": {
    "enabled": true,
    "min_interval_seconds": 21600,
    "min_compound_usd": 5.0,
    "gas_cost_multiplier": 3.0,
    "min_range_buffer_ratio": 0.1,
    "max_jobs_per_cycle": 1
  }
}
```

Aerodrome pools may declare `"position_strategy": "farm"` or `"fee"`. This value is advisory: the worker
logs a `STRATEGY_MISMATCH` but never stakes or unstakes an in-range NFT to enforce the setting. Pancake's
expected strategy is inferred from `pid`, while actual on-chain custody always controls contract routing.

For Aerodrome, `pool_address` is the only required protocol metadata address. The adapter resolves
`nft()` (NPM), `gauge()`, token addresses, fee, and tick spacing from the pool in a metadata Multicall.
Legacy `npm_address`, `staking_address`, token, fee, and tick-spacing fields remain supported as assertions
or verified fallbacks. See `sample_aerodrome_config.json` for a minimal example.

When enabled, each cycle runs the existing rebalance pass first. Eligible in-range positions then follow
`collect fee -> reprice -> swap excess -> increaseLiquidity`. Pancake positions may be staked or unstaked;
Aerodrome positions must be unstaked. Farm rewards, principal withdrawals, range changes, NFT mint/burn, and
wallet balances outside the compound reservation are never used.

The rebalance position index discovers both staked and wallet-owned NFTs. Auto-compound consumes that in-range
handoff and only revalidates each token ID; it does not enumerate the NPM or scan position caches a second time.

Run once with `--migrate` before enabling live execution so `configured_compound_jobs` and the rebalance
`restore_stake_mode` field exist. Dry-run performs
discovery, fee/profitability evaluation, swap quoting, and transaction simulation without creating a compound job.

## Notes

- PancakeSwap V3 supports both MasterChef-staked and wallet-owned positions.
- Aerodrome supports gauge-staked rebalancing and wallet-owned fee compounding; gauge-staked positions are skipped by auto-compound.
- `use_legacy_position_cache` is enabled by default to avoid a large first log sweep. Use `seed_token_ids` for canary runs or for positions missing from the legacy cache.
- Live execution requires `bot_wallet` to be the owner/user of the position being rebalanced. For multiple user wallets on the same pool, create one pool config entry per signer/private key.
- `--pnl-report` is read-only from the database perspective: it does not rebalance, migrate, or send transactions.
- Discord notifications are disabled for dry-run and are not sent by `--pnl-report`.
- `pnl.native_prices_usd` is only a fallback if market price APIs are unavailable.
