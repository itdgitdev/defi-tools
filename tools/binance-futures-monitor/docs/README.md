# Binance Futures Monitor

Interactive local worker for reading Binance USD-M and COIN-M positions and
publishing current-state data to MySQL. The project-root `.env` stores the full API
key and a user-selected prefix of the secret key. The remaining suffix is prompted
with hidden terminal input, validated by a signed Binance request, and the full
secret exists in process memory only.

See [OPERATIONS_GUIDE_VI.md](OPERATIONS_GUIDE_VI.md) for the detailed Vietnamese
security report, Binance API setup, IP restriction, and job runbook.
See [OPERATIONS_GUIDE_V2_VI.md](OPERATIONS_GUIDE_V2_VI.md) for the concise V2
operations and security runbook.

## Configure credentials

Merge the Binance entries from the project-root [`.env.example`](../../.env.example)
into your existing root `.env`. Do not overwrite existing database or rebalancer
settings. On a new checkout only, copy `.env.example` to `.env` and fill in the values.

For every configured account, store the full API key and any desired secret prefix
using the normalized account alias shown in `.env.example`. A blank prefix means
the full secret will be entered through the hidden terminal prompt.

The credential reader uses `dotenv_values(..., interpolate=False)` and takes values
only from the selected file, without environment overrides or fallback to the old
module `.env`. `--credentials-env` can explicitly select a separate file. Database
configuration still comes from the shared project environment.

The credential reader itself does not change `os.environ`, but the shared database
loader calls `load_dotenv()` during import. Consequently, API keys and prefixes in
the root `.env` may enter the environment of other project processes too. The suffix
and reconstructed full secret remain in the monitor's memory only.

When migrating an existing setup, compare duplicate variable names before merging
the old module `.env` into the root file; resolve differing values explicitly. After
verifying the merged credentials, remove the obsolete module file to avoid maintaining
two copies. Keep account aliases and the two workers' JSON configs unchanged.

## Run

From the tool directory, set up its own Python environment once:

```powershell
.\scripts\setup.ps1
```

You can also double-click `scripts\01_SETUP.bat`. Then run:

```powershell
.\scripts\run_binance_futures_monitor.ps1
```

The module-local `scripts\RUN_BINANCE_FUTURES_MONITOR.bat` runs the same launcher.
It uses this tool's `.venv\Scripts\python.exe` and repairs a missing or stale
installation before starting. By default it migrates tables and runs continuously in
an interactive terminal.
Use `-Once` for one cycle, `-SkipMigration` to skip migration, or `-ConfigFile` and
`-CredentialsEnv` to select other files. Relative launcher paths are tool-relative.

Equivalent direct CLI command (run from the tool directory):

```powershell
.\.venv\Scripts\python.exe -m binance_futures_monitor.cli `
  --config config\local.json `
  --migrate `
  --loop
```

The direct CLI's default credentials path is absolute and independent of the current
directory. Explicit relative CLI paths are relative to the current directory. For a
separate credentials file, add `--credentials-env C:\private\binance.env`.

The machine running this process must use an outbound IP allowed by each
Binance API key. Restarting the process requires entering each account's remaining
secret suffix again. Invalid signatures can be corrected through at most three
hidden input attempts before startup stops.

The configured account alias is a local stable name, not a Binance UID. Keep
the alias unchanged when rotating API keys.
