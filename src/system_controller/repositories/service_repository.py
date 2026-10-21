import logging
from pathlib import Path

from system_controller.models.service import InvalidServiceError, Service
from system_controller.repositories.json_file import read_json, write_json_atomic

logger = logging.getLogger(__name__)


class ServiceRepository:
    """格式：{"name": {"systemd": "x.service", "docker": "container"}}"""

    def __init__(self, path: str | Path = "database/service.json"):
        self.path = Path(path)
        self.services: dict[str, Service] = {}

        self._load()

    def _load(self) -> None:
        data = read_json(self.path)

        if not isinstance(data, dict): return

        for name, entry in data.items():
            try:
                self.services[name] = Service(name=name, targets=dict(entry))

            except (InvalidServiceError, TypeError, ValueError):
                logger.warning("略過無效的服務設定：%r -> %r", name, entry)

    def _commit(self, services: dict[str, Service]) -> None:
        # 寫入成功後才更新記憶體中的資料，失敗時維持原狀
        write_json_atomic(self.path, {
            service.name: {str(backend): target for backend, target in service.targets.items()}
            for service in services.values()
        })

        self.services = services

    def get(self, name: str) -> Service | None:
        return self.services.get(name)

    def get_all(self) -> list[Service]:
        return list(self.services.values())

    def add(self, service: Service) -> bool:
        if service.name in self.services: return False

        self._commit({**self.services, service.name: service})

        return True

    def replace(self, service: Service) -> None:
        self._commit({**self.services, service.name: service})

    def remove(self, name: str) -> bool:
        if name not in self.services: return False

        self._commit({key: value for key, value in self.services.items() if key != name})

        return True
