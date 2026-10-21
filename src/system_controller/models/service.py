import re
from dataclasses import dataclass
from enum import StrEnum


class Backend(StrEnum):
    SYSTEMD = "systemd"
    DOCKER = "docker"


# 服務顯示名稱：英數、底線、連字號，避免 Markdown / mention 注入
NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,32}")

# 所有 target 都不可以 "-" 開頭，避免被當成命令列選項
TARGET_PATTERNS: dict[Backend, re.Pattern[str]] = {
    # systemd unit：僅允許常見可操作的 unit 類型
    Backend.SYSTEMD: re.compile(r"[A-Za-z0-9][A-Za-z0-9:_.@\\-]{0,200}\.(service|socket|timer)"),
    # Docker 容器名稱規則（與 docker daemon 相同）
    Backend.DOCKER: re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}"),
}


class InvalidServiceError(ValueError):
    pass


def is_valid_name(name: str) -> bool:
    return isinstance(name, str) and NAME_PATTERN.fullmatch(name) is not None


def is_valid_target(backend: Backend, target: str) -> bool:
    pattern = TARGET_PATTERNS.get(backend)

    return pattern is not None and isinstance(target, str) and pattern.fullmatch(target) is not None


def validate_target(backend: Backend, target: str) -> str:
    if not is_valid_target(backend, target):
        raise InvalidServiceError(f"Invalid {backend} target: {target!r}")

    return target


def parse_backend(value: str) -> Backend:
    try:
        return Backend(value)

    except ValueError:
        raise InvalidServiceError(f"Unknown backend: {value!r}") from None


@dataclass(slots=True)
class Service:
    name: str
    target: str
    backend: Backend = Backend.SYSTEMD

    def __post_init__(self):
        if not is_valid_name(self.name):
            raise InvalidServiceError(
                f"Invalid service name: {self.name!r} (allowed: A-Z a-z 0-9 _ -, max 32 chars)"
            )

        self.backend = parse_backend(self.backend)
        validate_target(self.backend, self.target)
