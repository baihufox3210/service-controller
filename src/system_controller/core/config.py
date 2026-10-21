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


def _parse_bool(raw: str | None, key: str, *, default: bool) -> bool:
    if raw is None or raw.strip() == "": return default

    value = raw.strip().lower()

    if value in ("1", "true", "yes", "on"): return True
    if value in ("0", "false", "no", "off"): return False

    raise ConfigError(f"{key} 必須是 true 或 false，目前為 {raw!r}")


@dataclass(frozen=True, slots=True)
class Settings:
    token: str
    services_file: Path = Path("database/service.json")
    permissions_file: Path = Path("database/permissions.json")
    allowed_guild_ids: frozenset[int] = field(default_factory=frozenset)
    owner_user_ids: frozenset[int] = field(default_factory=frozenset)
    sudo_path: str = "/usr/bin/sudo"
    systemctl_path: str = "/usr/bin/systemctl"
    journalctl_path: str = "/usr/bin/journalctl"
    docker_path: str = "/usr/bin/docker"
    docker_use_sudo: bool = True


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
        sudo_path=env.get("SUDO_PATH") or "/usr/bin/sudo",
        systemctl_path=env.get("SYSTEMCTL_PATH") or "/usr/bin/systemctl",
        journalctl_path=env.get("JOURNALCTL_PATH") or "/usr/bin/journalctl",
        docker_path=env.get("DOCKER_PATH") or "/usr/bin/docker",
        docker_use_sudo=_parse_bool(env.get("DOCKER_USE_SUDO"), "DOCKER_USE_SUDO", default=True),
    )
