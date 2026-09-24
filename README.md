# defi-tools

Two locally installable tools extracted from `nft-pancake-monitor`. This folder has its own Git repository. It can be copied out of the parent directory without changing imports or launch paths.

## Layout

- `tools/binance-futures-monitor`: reads Binance USD-M and COIN-M positions and writes the current state to MySQL.
- `tools/configured-pool-rebalancer`: runs configured V3 rebalance and auto-compound cycles.
- `.env`: shared connection settings and credential prefixes for these two tools. It is ignored by Git.

Each tool has a separate `.venv`, `requirements.txt`, local JSON config, launcher, tests and ignored runtime directory. Both point to the same existing MySQL database. No Python module is shared between the two tools.

## Initial setup

1. Copy `.env.example` to `.env` and fill in the values, or use the private `.env` already transferred from the parent project. Never commit `.env`.
2. Review `tools/binance-futures-monitor/config/local.json` and `tools/configured-pool-rebalancer/config/local.json`. They are local files ignored by Git. Sample configs are provided next to them.
3. Run each tool's `scripts/setup.ps1`, or use the BAT setup/launcher. Installation is tool-specific.
4. Run the rebalancer `scripts/02_CHECK_CONFIG.bat`, followed by `-Mode DryRun` using `scripts/run_rebalancer.ps1`. Live modes require an interactive `LIVE` confirmation.
5. Start only one rebalancer for a given wallet. Stop the old parent-project worker before using this one's live launcher.

The Binance launcher is `tools/binance-futures-monitor/scripts/RUN_BINANCE_FUTURES_MONITOR.bat`. The rebalancer launchers are the numbered BAT files under its `scripts/` directory. Launchers resolve files from their own location; running them from another working directory is supported.

## CLI defaults

The default config for each CLI is its ignored `config/local.json`. The shared `.env` is resolved from this repository root. The rebalancer CLI defaults to dry-run; `--execute` sends real transactions. `--migrate` uses the existing MySQL table names and should be run only against the intended database.

PnL reports and Discord PnL notifications still read `wallet_nft_position` and `wallet_nft_summary` from the existing database. The snapshot writer remains in the parent project. Missing snapshots do not block rebalance, but reports may be incomplete and PnL notifications may remain pending. The preflight warns when optional PnL tables are absent.

The copied historical guides under each `docs/` directory describe the previous `latest_farms` layout. Use this README and the new tool-root READMEs for paths and commands in `defi-tools`.

## Cutover from the parent worker

The old worker can keep running while this repository is prepared. From the rebalancer tool directory, inspect cache and MySQL job status without copying anything:

```powershell
.\.venv\Scripts\python.exe scripts\transfer_runtime_state.py --source-cache C:\path\to\nft-pancake-monitor\latest_farms\configured_pool_rebalancer\cache
```

When the old worker has stopped and its current transaction has settled, repeat the command with `--copy --confirm-worker-stopped`. The script copies only configured pools' cache files and refuses to overwrite different destination files. If `use_legacy_position_cache` is enabled, also provide `--source-legacy-cache` pointing to the old `latest_farms\positions_cache` directory. Review its job-status output, then run `scripts\02_CHECK_CONFIG.bat` and `scripts\run_rebalancer.ps1 -Mode DryRun` before starting a new live launcher. Keep the parent launchers available for rollback, but never run both live workers for the same wallet.
