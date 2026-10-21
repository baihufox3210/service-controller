import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(Exception):
    pass


def _parse_ids(raw: str | None, key: str) -> frozenset[int]:
    if not raw: return frozenset()

    ids: set[int] = set()

    for part in raw.replace(" ", "").split(","):
        if not part: continue

        if not part.isdigit():
            raise ConfigError(f"{key} 包含無效的 ID：{part!r}")

        ids.add(int(part))

    return frozenset(ids)


@dataclass(frozen=True, slots=True)
class Settings:
    token: str
    services_file: Path = Path("database/service.json")
    permissions_file: Path = Path("database/permissions.json")
    allowed_guild_ids: frozenset[int] = field(default_factory=frozenset)
    owner_user_ids: frozenset[int] = field(default_factory=frozenset)


def load_settings(env: dict[str, str] | None = None) -> Settings:
    if env is None:
        load_dotenv()
        env = dict(os.environ)

    token = env.get("BOT_TOKEN")

    if not token:
        raise ConfigError("未設定 BOT_TOKEN，請在 .env 或環境變數中提供 Discord Bot Token。")

    def ids(key: str) -> frozenset[int]:
        return _parse_ids(env.get(key), key)

    return Settings(
        token=token,
        services_file=Path(env.get("SERVICES_FILE") or "database/service.json"),
        permissions_file=Path(env.get("PERMISSIONS_FILE") or "database/permissions.json"),
        allowed_guild_ids=ids("ALLOWED_GUILD_IDS"),
        owner_user_ids=ids("OWNER_USER_IDS"),
    )
