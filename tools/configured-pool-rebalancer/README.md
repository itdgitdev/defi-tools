# Configured Pool Rebalancer

From this tool directory, run `scripts/01_SETUP.bat`, then `scripts/02_CHECK_CONFIG.bat`. Use `scripts/03_RUN_ONE_CYCLE.bat` for one interactive live cycle and `scripts/04_RUN_LOOP.bat` for an interactive live loop. Both require typing `LIVE`. The config is `config/local.json`, shared DB and RPC settings are in `../../.env`, and cache and logs are in `runtime/`.

Use `scripts/run_rebalancer.ps1 -Mode DryRun` to inspect plans without sending transactions. The direct CLI remains dry-run by default: `.venv\Scripts\python.exe -m configured_pool_rebalancer.cli --config config/local.json`. `-Mode Report` reads PnL data produced by the existing snapshot writer. The historical guides are in `docs/` and the sample configs are under `config/`.

If KyberSwap blocks direct API access, set `SWAPPER_KYBER_PROXY_URL` in the repo-root `.env` to an HTTP or HTTPS proxy URL (for example, `http://user:password@proxy.example:8080`). Both Kyber route and build requests use that proxy; 0x and OKX do not. An empty value keeps the existing Requests behavior, including any system proxy settings. Keep proxy credentials out of logs and committed files. A proxy does not guarantee Kyber will accept the request, so verify a route and build through it before relying on Kyber for live swaps.
