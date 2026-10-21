import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from system_controller.adapters.base import CommandResult, ServiceBackend
from system_controller.adapters.docker import DockerAdapter
from system_controller.adapters.systemd import SystemdAdapter
from system_controller.models.service import Backend, Service, parse_backend
from system_controller.repositories.repository import ServiceRepository


@dataclass(slots=True)
class ServiceOperationResult:
    success: bool
    service: Service | None = None
    message: str = ""


@dataclass(slots=True)
class ServiceStatus:
    service: Service
    active: str
    enabled: str


@dataclass(slots=True)
class ServiceLogs:
    service: Service
    content: str
    success: bool = True


class SystemService:
    def __init__(
        self,
        repository: ServiceRepository | None = None,
        systemd: ServiceBackend | None = None,
        docker: ServiceBackend | None = None,
    ):
        self.repository = repository or ServiceRepository()
        self.backends: Mapping[Backend, ServiceBackend] = {
            Backend.SYSTEMD: systemd or SystemdAdapter(),
            Backend.DOCKER: docker or DockerAdapter(),
        }
        # 同一個服務的操作依序執行，避免 start / stop 互相競爭
        self._locks: dict[str, asyncio.Lock] = {}

    def get(self, name: str) -> Service | None:
        return self.repository.get(name)

    def get_all(self) -> list[Service]:
        return self.repository.get_all()

    def _backend(self, service: Service) -> ServiceBackend:
        return self.backends[service.backend]

    def _lock(self, name: str) -> asyncio.Lock:
        return self._locks.setdefault(name, asyncio.Lock())

    async def _execute(
        self,
        name: str,
        operation: Callable[[ServiceBackend], Callable[[str], Awaitable[CommandResult]]],
        success_message: str,
    ) -> ServiceOperationResult:
        service = self.repository.get(name)

        if service is None:
            return ServiceOperationResult(
                success=False,
                message=f"Unknown service: {name}"
            )

        async with self._lock(service.name):
            result = await operation(self._backend(service))(service.target)

        if not result.success:
            return ServiceOperationResult(
                success=False,
                service=service,
                message=result.stderr.strip() or result.stdout.strip() or "Operation failed"
            )

        return ServiceOperationResult(
            success=True,
            service=service,
            message=success_message
        )

    async def start(self, name: str) -> ServiceOperationResult:
        return await self._execute(
            name,
            lambda backend: backend.start,
            "Service started successfully."
        )

    async def stop(self, name: str) -> ServiceOperationResult:
        return await self._execute(
            name,
            lambda backend: backend.stop,
            "Service stopped successfully."
        )

    async def restart(self, name: str) -> ServiceOperationResult:
        return await self._execute(
            name,
            lambda backend: backend.restart,
            "Service restarted successfully."
        )

    async def enable(self, name: str) -> ServiceOperationResult:
        return await self._execute(
            name,
            lambda backend: backend.enable,
            "Service enabled successfully."
        )

    async def disable(self, name: str) -> ServiceOperationResult:
        return await self._execute(
            name,
            lambda backend: backend.disable,
            "Service disabled successfully."
        )

    async def status(self, name: str) -> ServiceStatus | None:
        service = self.repository.get(name)

        if service is None:
            return None

        state = await self._backend(service).state(service.target)

        return ServiceStatus(
            service=service,
            active=state.active,
            enabled=state.enabled,
        )

    async def logs(self, name: str, lines: int = 50) -> ServiceLogs | None:
        service = self.repository.get(name)

        if service is None: return None

        result = await self._backend(service).logs(service.target, lines)

        if not result.success:
            return ServiceLogs(
                service=service,
                content=result.stderr.strip() or result.stdout.strip() or "Failed to retrieve logs.",
                success=False,
            )

        return ServiceLogs(
            service=service,
            content=result.stdout.strip()
        )

    def add(self, name: str, target: str, backend: Backend | str = Backend.SYSTEMD) -> bool:
        """名稱或 target 不合法時拋出 InvalidServiceError；名稱重複時回傳 False。"""
        service = Service(
            name=name,
            target=target,
            backend=parse_backend(backend),
        )

        return self.repository.add(service)

    def remove(self, name: str) -> bool:
        removed = self.repository.remove(name)

        if removed:
            self._locks.pop(name, None)

        return removed
