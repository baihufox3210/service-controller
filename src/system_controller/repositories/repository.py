import contextlib
import json
import logging
import os
import tempfile
import threading
from pathlib import Path

from system_controller.models.service import Backend, InvalidServiceError, Service, parse_backend

logger = logging.getLogger(__name__)

class ServiceRepository:
    def __init__(self, path: str | Path = "database/service.json"):
        self.path = Path(path)
        self.services: dict[str, Service] = {}
        self._lock = threading.Lock()

        self._load()

    def _load(self) -> None:
        if not self.path.exists(): return

        try:
            with self.path.open("r", encoding="utf-8") as file:
                data = json.load(file)

        except(json.JSONDecodeError, OSError):
            logger.exception("無法讀取服務清單 %s", self.path)
            return

        if not isinstance(data, dict): return

        services = {}

        for name, entry in data.items():
            try:
                services[name] = self._decode(name, entry)

            except (InvalidServiceError, TypeError, KeyError):
                logger.warning("略過無效的服務設定：%r -> %r", name, entry)

        self.services = services

    @staticmethod
    def _decode(name: str, entry) -> Service:
        # 舊格式 {"name": "unit.service"} 視為 systemd 服務
        if isinstance(entry, str):
            return Service(name=name, target=entry, backend=Backend.SYSTEMD)

        if not isinstance(entry, dict) or not isinstance(entry["target"], str):
            raise TypeError("invalid entry")

        return Service(name=name, target=entry["target"], backend=parse_backend(entry.get("backend", "systemd")))

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            service.name: {"backend": str(service.backend), "target": service.target}
            for service in self.services.values()
        }

        # 先寫入暫存檔再原子性取代，避免寫到一半當機導致檔案損毀
        fd, tmp_path = tempfile.mkstemp(dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp")

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(data, file, indent=4, ensure_ascii=False)
                file.flush()
                os.fsync(file.fileno())

            os.chmod(tmp_path, 0o600)
            os.replace(tmp_path, self.path)

        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp_path)

            raise

    def get(self, name: str) -> Service | None:
        return self.services.get(name)

    def get_all(self) -> list[Service]:
        return list(self.services.values())

    def add(self, service: Service) -> bool:
        with self._lock:
            if service.name in self.services: return False

            self.services[service.name] = service

            try:
                self._save()

            except OSError:
                del self.services[service.name]
                raise

            return True

    def remove(self, name: str) -> bool:
        with self._lock:
            if name not in self.services: return False

            service = self.services.pop(name)

            try:
                self._save()

            except OSError:
                self.services[name] = service
                raise

            return True

