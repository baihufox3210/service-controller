import logging
from collections.abc import Iterable
from pathlib import Path

from system_controller.models.permission import Grant, Permission, SubjectType
from system_controller.repositories.json_file import read_json, write_json_atomic

logger = logging.getLogger(__name__)

GrantKey = tuple[SubjectType, int, str | None]


class PermissionRepository:
    """格式：{"grants": [{"type": "user", "id": 1, "level": "admin", "service": null}]}"""

    def __init__(self, path: str | Path = "database/permissions.json"):
        self.path = Path(path)
        self.grants: dict[GrantKey, Grant] = {}

        self._load()

    def _load(self) -> None:
        data = read_json(self.path)

        if not isinstance(data, dict): return

        for entry in data.get("grants", []):
            try:
                grant = Grant(
                    subject_type=SubjectType(entry["type"]),
                    subject_id=int(entry["id"]),
                    level=Permission[entry["level"].upper()],
                    service=entry.get("service"),
                )

            except (KeyError, TypeError, ValueError, AttributeError):
                logger.warning("略過無效的權限設定：%r", entry)
                continue

            self.grants[grant.key] = grant

    def _commit(self, grants: dict[GrantKey, Grant]) -> None:
        write_json_atomic(self.path, {
            "grants": [
                {
                    "type": str(grant.subject_type),
                    "id": grant.subject_id,
                    "level": grant.level.name.lower(),
                    "service": grant.service,
                }
                for grant in grants.values()
            ]
        })

        self.grants = grants

    def get(self, subject_type: SubjectType, subject_id: int, service: str | None = None) -> Grant | None:
        return self.grants.get((subject_type, subject_id, service))

    def find(self, service: str | None = None) -> list[Grant]:
        """service 為 None 時列出全部授權。"""
        return [grant for grant in self.grants.values() if service is None or grant.service == service]

    def matching(self, user_id: int, role_ids: Iterable[int], service: str | None = None) -> list[Grant]:
        """回傳套用到此使用者的全域授權，以及指定服務的授權。"""
        subjects = {(SubjectType.USER, user_id), *((SubjectType.ROLE, role_id) for role_id in role_ids)}

        return [
            grant for grant in self.grants.values()
            if (grant.subject_type, grant.subject_id) in subjects and grant.service in (None, service)
        ]

    def set(self, grant: Grant) -> None:
        self._commit({**self.grants, grant.key: grant})

    def remove(self, subject_type: SubjectType, subject_id: int, service: str | None = None) -> Grant | None:
        grant = self.get(subject_type, subject_id, service)

        if grant is not None:
            self._commit({key: value for key, value in self.grants.items() if key != grant.key})

        return grant

    def remove_service(self, service: str) -> None:
        if any(grant.service == service for grant in self.grants.values()):
            self._commit({key: value for key, value in self.grants.items() if value.service != service})
