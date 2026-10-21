import asyncio
from collections.abc import Mapping
from dataclasses import dataclass

from system_controller.adapters.base import ServiceBackend
from system_controller.adapters.docker import DockerAdapter
from system_controller.adapters.systemd import SystemdAdapter
from system_controller.models.service import Action, Backend, InvalidServiceError, Service
from system_controller.repositories.service_repository import ServiceRepository

# 關閉類操作以相反順序執行（先停 docker，再停 systemd）
REVERSED_ACTIONS = {Action.STOP, Action.DISABLE}


class ServiceNotFound(LookupError):
    def __init__(self, name: str):
        super().__init__(f"找不到服務 {name}")


@dataclass(slots=True)
class BackendResult:
    backend: Backend
    target: str
    success: bool
    message: str = ""


@dataclass(slots=True)
class BackendStatus:
    backend: Backend
    target: str
    active: str
    enabled: str
    error: str | None = None


class SystemService:
    def __init__(
        self,
        repository: ServiceRepository | None = None,
        backends: Mapping[Backend, ServiceBackend] | None = None,
    ):
        self.repository = repository or ServiceRepository()
        self.backends = backends or {
            Backend.SYSTEMD: SystemdAdapter(),
            Backend.DOCKER: DockerAdapter(),
        }
        # 同一個服務的操作依序執行，避免 start / stop 互相競爭
        self._locks: dict[str, asyncio.Lock] = {}

    def get(self, name: str) -> Service | None:
        return self.repository.get(name)

    def get_all(self) -> list[Service]:
        return self.repository.get_all()

    def _require(self, name: str) -> Service:
        service = self.repository.get(name)

        if service is None:
            raise ServiceNotFound(name)

        return service

    async def control(self, name: str, action: Action, scope: Backend | None = None) -> list[BackendResult]:
        """執行操作；服務不存在拋出 ServiceNotFound，未設定指定後端拋出 InvalidServiceError。"""
        service = self._require(name)
        targets = service.select(scope)

        if action in REVERSED_ACTIONS:
            targets.reverse()

        results = []

        async with self._locks.setdefault(service.name, asyncio.Lock()):
            for backend, target in targets:
                result = await self.backends[backend].control(action, target)
                results.append(BackendResult(
                    backend=backend,
                    target=target,
                    success=result.success,
                    message="" if result.success else (result.stderr.strip() or result.stdout.strip() or "Operation failed"),
                ))

        return results

    async def status(self, name: str, scope: Backend | None = None) -> list[BackendStatus]:
        statuses = []

        for backend, target in self._require(name).select(scope):
            state = await self.backends[backend].state(target)
            statuses.append(BackendStatus(backend, target, state.active, state.enabled, state.error))

        return statuses

    async def logs(self, name: str, scope: Backend | None = None, lines: int = 50) -> list[BackendResult]:
        logs = []

        for backend, target in self._require(name).select(scope):
            result = await self.backends[backend].logs(target, lines)
            content = result.stdout.strip() if result.success else (result.stderr.strip() or result.stdout.strip())
            logs.append(BackendResult(backend, target, result.success, content or "(無輸出)"))

        return logs

    def add(self, name: str, targets: Mapping[Backend, str]) -> bool:
        """名稱或 target 不合法時拋出 InvalidServiceError；名稱重複時回傳 False。"""
        return self.repository.add(Service(name=name, targets=dict(targets)))

    def set_target(self, name: str, backend: Backend, target: str) -> Service:
        service = self._require(name)
        updated = Service(name=name, targets={**service.targets, backend: target})
        self.repository.replace(updated)

        return updated

    def unset_target(self, name: str, backend: Backend) -> Service:
        service = self._require(name)

        if backend not in service.targets:
            raise InvalidServiceError(f"服務 {name} 未設定 {backend}")

        # 移除最後一個後端時，Service 驗證會拋出 InvalidServiceError
        updated = Service(name=name, targets={k: v for k, v in service.targets.items() if k != backend})
        self.repository.replace(updated)

        return updated

    def remove(self, name: str) -> bool:
        self._locks.pop(name, None)

        return self.repository.remove(name)
