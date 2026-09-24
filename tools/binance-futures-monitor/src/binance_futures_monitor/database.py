import os

import mysql.connector
from dotenv import load_dotenv

from .paths import ENV_FILE


load_dotenv(ENV_FILE, override=False)


def get_connection():
    prefix = "LOCAL" if os.getenv("ENV", "local") == "local" else "SERVER"
    config = {
        "host": os.getenv(f"{prefix}_DB_HOST"),
        "user": os.getenv(f"{prefix}_DB_USER"),
        "password": os.getenv(f"{prefix}_DB_PASS"),
        "database": os.getenv(f"{prefix}_DB_NAME"),
        "port": int(os.getenv(f"{prefix}_DB_PORT", "3306")),
        "ssl_disabled": os.getenv(f"{prefix}_DB_SSL_DISABLED", "true").lower() == "true",
    }
    return mysql.connector.connect(**config)
