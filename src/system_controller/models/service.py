import re
from dataclasses import dataclass, field
from enum import StrEnum


class Backend(StrEnum):
    SYSTEMD = "systemd"
    DOCKER = "docker"


class Action(StrEnum):
    START = "start"
    STOP = "stop"
    RESTART = "restart"
    ENABLE = "enable"
    DISABLE = "disable"


# 服務名稱：英數、底線、連字號，避免 Markdown / mention 注入
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


def validate_target(backend: Backend, target: str) -> str:
    if not isinstance(target, str) or TARGET_PATTERNS[backend].fullmatch(target) is None:
        raise InvalidServiceError(f"無效的 {backend} 名稱：{target!r}")

    return target


@dataclass(slots=True)
class Service:
    """一個服務可以同時對應 systemd unit 與 Docker 容器。"""

    name: str
    targets: dict[Backend, str] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.name, str) or NAME_PATTERN.fullmatch(self.name) is None:
            raise InvalidServiceError(f"無效的服務名稱：{self.name!r}（僅限 A-Z a-z 0-9 _ -，最多 32 字）")

        try:
            targets = {Backend(backend): target for backend, target in self.targets.items()}

        except ValueError as e:
            raise InvalidServiceError(f"未知的後端：{e}") from None

        if not targets:
            raise InvalidServiceError("至少需要設定 systemd unit 或 Docker 容器其中之一")

        for backend, target in targets.items():
            validate_target(backend, target)

        # 固定順序：systemd 在前、docker 在後
        self.targets = {backend: targets[backend] for backend in Backend if backend in targets}

    def select(self, scope: Backend | None = None) -> list[tuple[Backend, str]]:
        """scope 為 None 代表全部已設定的後端。"""
        if scope is None:
            return list(self.targets.items())

        if scope not in self.targets:
            raise InvalidServiceError(f"服務 {self.name} 未設定 {scope}")

        return [(scope, self.targets[scope])]
