from pathlib import Path


TOOL_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[4]
ENV_FILE = REPO_ROOT / ".env"
LOCAL_CONFIG = TOOL_ROOT / "config" / "local.json"
SAMPLE_CONFIG = TOOL_ROOT / "config" / "sample.json"
RUNTIME_DIR = TOOL_ROOT / "runtime"
CACHE_DIR = RUNTIME_DIR / "cache"
LEGACY_CACHE_DIR = RUNTIME_DIR / "legacy-position-cache"
LOG_DIR = RUNTIME_DIR / "logs"
