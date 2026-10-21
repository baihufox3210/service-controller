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

# 允許操作的 systemd unit 類型；未寫副檔名時預設為 .service
SYSTEMD_SUFFIXES = ("service", "socket", "timer")
DEFAULT_SYSTEMD_SUFFIX = "service"

# 其他 systemd unit 類型（target、mount 等）一律拒絕，不會被補上 .service
OTHER_SYSTEMD_SUFFIXES = ("target", "mount", "automount", "swap", "path", "slice", "scope", "device")

# 所有 target 都不可以 "-" 開頭，避免被當成命令列選項
TARGET_PATTERNS: dict[Backend, re.Pattern[str]] = {
    # systemd unit：僅允許 SYSTEMD_SUFFIXES 中的 unit 類型
    Backend.SYSTEMD: re.compile(rf"[A-Za-z0-9][A-Za-z0-9:_.@\\-]{{0,200}}\.({'|'.join(SYSTEMD_SUFFIXES)})"),
    # Docker 容器名稱規則（與 docker daemon 相同）
    Backend.DOCKER: re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}"),
}


class InvalidServiceError(ValueError):
    pass


def validate_target(backend: Backend, target: str) -> str:
    if not isinstance(target, str) or TARGET_PATTERNS[backend].fullmatch(target) is None:
        raise InvalidServiceError(f"無效的 {backend} 名稱：{target!r}")

    return target


def normalize_target(backend: Backend, target: str) -> str:
    """systemd unit 沒有寫副檔名時補上 .service，並驗證格式。"""
    if backend is not Backend.SYSTEMD or not isinstance(target, str):
        return validate_target(backend, target)

    suffix = target.rpartition(".")[2] if "." in target else ""

    if suffix in OTHER_SYSTEMD_SUFFIXES:
        raise InvalidServiceError(f"不支援的 systemd unit 類型：.{suffix}（僅限 {', '.join(SYSTEMD_SUFFIXES)}）")

    try:
        return validate_target(backend, target if suffix in SYSTEMD_SUFFIXES else f"{target}.{DEFAULT_SYSTEMD_SUFFIX}")

    except InvalidServiceError:
        raise InvalidServiceError(f"無效的 systemd 名稱：{target!r}") from None


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

        # 固定順序：systemd 在前、docker 在後
        self.targets = {backend: normalize_target(backend, targets[backend]) for backend in Backend if backend in targets}

    def select(self, scope: Backend | None = None) -> list[tuple[Backend, str]]:
        """scope 為 None 代表全部已設定的後端。"""
        if scope is None:
            return list(self.targets.items())

        if scope not in self.targets:
            raise InvalidServiceError(f"服務 {self.name} 未設定 {scope}")

        return [(scope, self.targets[scope])]
