# Binance Futures Monitor

From this tool directory, run `scripts/setup.ps1` once, then `scripts/RUN_BINANCE_FUTURES_MONITOR.bat`. The launcher loops by default and prompts for the remaining API-secret suffix. Add `-Once` to the PowerShell launcher for one cycle. The default config is `config/local.json`, and shared credentials and MySQL settings are read from `../../.env`.

Direct CLI: `.venv\Scripts\python.exe -m binance_futures_monitor.cli --config config/local.json --migrate --loop`. The direct CLI also accepts `--credentials-env` for a different Binance credential file. Use `config/sample.json` as a template. Historical operational guides are in `docs/`.
