from system_controller.adapters.base import BackendState, CommandAdapter, CommandResult, clamp_lines
from system_controller.models.service import Action, Backend


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

    async def _systemctl(self, verb: str, unit: str, *, privileged: bool) -> CommandResult:
        if error := self._invalid_target(unit): return error

        # "--" 確保 unit 一定被解讀為參數而非選項
        command = (self.systemctl_path, verb, "--", unit)

        if privileged:
            command = (self.sudo_path, "-n", *command)

        return await self._run(*command)

    async def control(self, action: Action, unit: str) -> CommandResult:
        # Action 的值與 systemctl 子指令相同
        return await self._systemctl(Action(action).value, unit, privileged=True)

    async def state(self, unit: str) -> BackendState:
        # 查詢類指令不需要 root 權限，不經過 sudo 以縮小權限範圍
        active = await self._systemctl("is-active", unit, privileged=False)
        enabled = await self._systemctl("is-enabled", unit, privileged=False)

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
