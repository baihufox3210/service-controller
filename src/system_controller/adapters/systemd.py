from system_controller.adapters.base import BackendState, CommandAdapter, CommandResult, clamp_lines
from system_controller.models.service import Action, Backend


class SystemdAdapter(CommandAdapter):
    """透過掛載進容器的 D-Bus socket 操作主機的 systemd；權限由主機的 polkit 規則決定。"""

    backend = Backend.SYSTEMD

    def __init__(self, systemctl_path: str = "/usr/bin/systemctl", journalctl_path: str = "/usr/bin/journalctl"):
        self.systemctl_path = systemctl_path
        self.journalctl_path = journalctl_path

    async def _systemctl(self, verb: str, unit: str) -> CommandResult:
        if error := self._invalid_target(unit): return error

        # "--" 確保 unit 一定被解讀為參數而非選項；--no-ask-password 避免等待互動式驗證
        return await self._run(self.systemctl_path, "--no-ask-password", verb, "--", unit)

    async def control(self, action: Action, unit: str) -> CommandResult:
        # Action 的值與 systemctl 子指令相同
        return await self._systemctl(Action(action).value, unit)

    async def state(self, unit: str) -> BackendState:
        active = await self._systemctl("is-active", unit)
        enabled = await self._systemctl("is-enabled", unit)

        # 服務未執行時 is-active 也會回傳非 0，但仍會輸出狀態；沒有輸出才代表查詢失敗
        errors = [result.stderr.strip() or "查詢失敗" for result in (active, enabled) if not result.stdout.strip()]

        return BackendState(
            active=active.stdout.strip() or "unknown",
            enabled=enabled.stdout.strip() or "unknown",
            error="\n".join(dict.fromkeys(errors)) or None,
        )

    async def logs(self, unit: str, lines: int = 50) -> CommandResult:
        if error := self._invalid_target(unit): return error

        return await self._run(
            self.journalctl_path, f"--unit={unit}", "-n", str(clamp_lines(lines)), "--no-pager", "-o", "short-iso",
            timeout=10
        )
