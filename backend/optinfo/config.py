"""Configuration never embeds credentials in source or client packages."""
from dataclasses import dataclass
from datetime import datetime
import os
from pathlib import Path
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


def now_shanghai() -> datetime:
    return datetime.now(SHANGHAI)


@dataclass(frozen=True)
class Settings:
    db_path: Path
    chat_dir: Path
    token: str = ""
    cors_origins: tuple[str, ...] = ()

    @classmethod
    def from_env(cls) -> "Settings":
        data = Path(os.environ.get("OPTINFO_DATA_DIR", Path(__file__).resolve().parents[1] / "data"))
        token = os.environ.get("OPTINFO_TOKEN", "").strip()
        token_file = os.environ.get("OPTINFO_TOKEN_FILE")
        if not token and token_file:
            token = Path(token_file).read_text(encoding="utf-8").strip()
        return cls(
            db_path=Path(os.environ.get("OPTINFO_DB_PATH", data / "optinfo.sqlite3")),
            chat_dir=Path(os.environ.get("OPTINFO_CHAT_DIR", data / "chats")),
            token=token,
            cors_origins=tuple(x.strip() for x in os.environ.get("OPTINFO_CORS_ORIGINS", "").split(",") if x.strip()),
        )
