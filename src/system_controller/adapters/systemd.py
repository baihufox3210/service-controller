from system_controller.adapters.base import BackendState, CommandAdapter, CommandResult, clamp_lines
from system_controller.models.service import Backend


class SystemdAdapter(CommandAdapter):
    backend = Backend.SYSTEMD

    def __init__(
        self,
        sudo_path: str = "/usr/bin/sudo",
        systemctl_path: str = "/usr/bin/systemctl",
        journalctl_path: str = "/usr/bin/journalctl",
    ):
        self.sudo_path = sudo_path
        self.systemctl_path = systemctl_path
        self.journalctl_path = journalctl_path

    async def _systemctl(self, action: str, unit: str, *, privileged: bool = True, timeout: float = 15) -> CommandResult:
        if error := self._invalid_target(unit): return error

        # "--" 確保 unit 一定被解讀為參數而非選項
        command = (self.systemctl_path, action, "--", unit)

        if privileged:
            command = (self.sudo_path, "-n", *command)

        return await self._run(*command, timeout=timeout)

    async def start(self, unit: str) -> CommandResult:
        return await self._systemctl("start", unit)

    async def stop(self, unit: str) -> CommandResult:
        return await self._systemctl("stop", unit)

    async def restart(self, unit: str) -> CommandResult:
        return await self._systemctl("restart", unit)

    async def enable(self, unit: str) -> CommandResult:
        return await self._systemctl("enable", unit)

    async def disable(self, unit: str) -> CommandResult:
        return await self._systemctl("disable", unit)

    # 查詢類指令不需要 root 權限，不經過 sudo 以縮小權限範圍
    async def is_active(self, unit: str) -> CommandResult:
        return await self._systemctl("is-active", unit, privileged=False)

    async def is_enabled(self, unit: str) -> CommandResult:
        return await self._systemctl("is-enabled", unit, privileged=False)

    async def state(self, unit: str) -> BackendState:
        active = await self.is_active(unit)
        enabled = await self.is_enabled(unit)

        return BackendState(
            active=active.stdout.strip() or "unknown",
            enabled=enabled.stdout.strip() or "unknown",
        )

    async def logs(self, unit: str, lines: int = 50) -> CommandResult:
        if error := self._invalid_target(unit): return error

        return await self._run(
            self.journalctl_path, f"--unit={unit}", "-n", str(clamp_lines(lines)), "--no-pager", "-o", "short-iso",
            timeout=10
        )
