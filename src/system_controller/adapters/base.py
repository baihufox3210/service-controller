import asyncio
import contextlib
from dataclasses import dataclass
from typing import Protocol

from system_controller.models.service import Backend, InvalidServiceError, validate_target

MAX_LOG_LINES = 500


@dataclass(slots=True)
class CommandResult:
    code: int
    stdout: str
    stderr: str

    @property
    def success(self) -> bool:
        return self.code == 0


@dataclass(slots=True)
class BackendState:
    active: str
    enabled: str


class ServiceBackend(Protocol):
    async def start(self, target: str) -> CommandResult: ...
    async def stop(self, target: str) -> CommandResult: ...
    async def restart(self, target: str) -> CommandResult: ...
    async def enable(self, target: str) -> CommandResult: ...
    async def disable(self, target: str) -> CommandResult: ...
    async def state(self, target: str) -> BackendState: ...
    async def logs(self, target: str, lines: int = 50) -> CommandResult: ...


def clamp_lines(lines: int) -> int:
    return max(1, min(int(lines), MAX_LOG_LINES))


class CommandAdapter:
    backend: Backend

    async def _run(self, *args: str, timeout: float = 15, merge_stderr: bool = False) -> CommandResult:
        try:
            process = await asyncio.create_subprocess_exec(
                *args,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT if merge_stderr else asyncio.subprocess.PIPE,
            )

        except OSError as e:
            return CommandResult(
                code=-1, stdout="", stderr=str(e)
            )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )

        except asyncio.TimeoutError:
            # 逾時後必須終止子程序，否則會留下殭屍程序
            with contextlib.suppress(ProcessLookupError):
                process.kill()

            await process.wait()

            return CommandResult(
                code=-1, stdout="", stderr="Command timed out."
            )

        return CommandResult(
            code=process.returncode if process.returncode is not None else -1,
            stdout=(stdout or b"").decode(errors="replace"),
            stderr=(stderr or b"").decode(errors="replace")
        )

    def _invalid_target(self, target: str) -> CommandResult | None:
        try:
            validate_target(self.backend, target)

        except InvalidServiceError as e:
            return CommandResult(code=-1, stdout="", stderr=str(e))

        return None
