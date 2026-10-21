from dataclasses import dataclass
from enum import IntEnum, StrEnum


class Permission(IntEnum):
    NONE = 0
    VIEWER = 1      # list / status / logs
    OPERATOR = 2    # start / stop / restart / enable / disable
    ADMIN = 3       # 管理服務、授予操作者 / 檢視者
    OWNER = 4       # 由 .env 設定，可管理管理員

    @property
    def label(self) -> str:
        return PERMISSION_LABELS[self]


PERMISSION_LABELS = {
    Permission.NONE: "無",
    Permission.VIEWER: "檢視者",
    Permission.OPERATOR: "操作者",
    Permission.ADMIN: "管理員",
    Permission.OWNER: "擁有者",
}

GRANTABLE = (Permission.VIEWER, Permission.OPERATOR, Permission.ADMIN)

# 單一服務授權最高只到操作者
SERVICE_GRANTABLE = (Permission.VIEWER, Permission.OPERATOR)


class SubjectType(StrEnum):
    USER = "user"
    ROLE = "role"


@dataclass(frozen=True, slots=True)
class Grant:
    subject_type: SubjectType
    subject_id: int
    level: Permission
    service: str | None = None  # None 代表全域

    def __post_init__(self):
        allowed = SERVICE_GRANTABLE if self.service else GRANTABLE

        if self.level not in allowed:
            raise ValueError(f"無法授予「{Permission(self.level).label}」" + ("給單一服務" if self.service else ""))

    @property
    def key(self) -> tuple[SubjectType, int, str | None]:
        return self.subject_type, self.subject_id, self.service

    @property
    def mention(self) -> str:
        return f"<@{self.subject_id}>" if self.subject_type is SubjectType.USER else f"<@&{self.subject_id}>"
