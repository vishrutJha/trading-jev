from __future__ import annotations
import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: str | Path = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k.strip(), v)


@dataclass(frozen=True)
class Settings:
    db_path: str = "data/backtester.sqlite"
    host: str = "127.0.0.1"
    port: int = 8000
    jev_model: str = "jev-latest"

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        return cls(
            db_path=os.getenv("APP_DB_PATH", "data/backtester.sqlite"),
            host=os.getenv("APP_HOST", "127.0.0.1"),
            port=int(os.getenv("APP_PORT", "8000")),
            jev_model=os.getenv("TYPESAFE_DEFAULT_MODEL", "jev-latest"),
        )
