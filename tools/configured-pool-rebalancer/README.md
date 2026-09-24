# Configured Pool Rebalancer

From this tool directory, run `scripts/01_SETUP.bat`, then `scripts/02_CHECK_CONFIG.bat`. Use `scripts/03_RUN_ONE_CYCLE.bat` for one interactive live cycle and `scripts/04_RUN_LOOP.bat` for an interactive live loop. Both require typing `LIVE`. The config is `config/local.json`, shared DB and RPC settings are in `../../.env`, and cache and logs are in `runtime/`.

Use `scripts/run_rebalancer.ps1 -Mode DryRun` to inspect plans without sending transactions. The direct CLI remains dry-run by default: `.venv\Scripts\python.exe -m configured_pool_rebalancer.cli --config config/local.json`. `-Mode Report` reads PnL data produced by the existing snapshot writer. The historical guides are in `docs/` and the sample configs are under `config/`.
