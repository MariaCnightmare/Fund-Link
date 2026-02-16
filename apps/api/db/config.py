from __future__ import annotations

import os

DEFAULT_DATABASE_URL = "postgresql+asyncpg://fundlink:fundlink@localhost:5433/fundlink"


def get_database_url() -> str:
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
