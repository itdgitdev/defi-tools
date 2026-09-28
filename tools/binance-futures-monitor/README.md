# Binance Futures Monitor

From this tool directory, run `scripts/01_SETUP.bat` once, then `scripts/RUN_BINANCE_FUTURES_MONITOR.bat`. The setup creates this tool's own `.venv` and installs its dependencies; no other tool needs to be set up first. The launcher checks that its environment imports the monitor from this checkout and repairs a missing or stale installation automatically. It loops by default and prompts for the remaining API-secret suffix. Add `-Once` to the PowerShell launcher for one cycle. The default config is `config/local.json`, and shared credentials and MySQL settings are read from `../../.env`.

Direct CLI: `.venv\Scripts\python.exe -m binance_futures_monitor.cli --config config/local.json --migrate --loop`. The direct CLI also accepts `--credentials-env` for a different Binance credential file. Use `config/sample.json` as a template. Historical operational guides are in `docs/`.
