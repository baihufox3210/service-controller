from system_controller.adapters.base import BackendState, CommandAdapter, CommandResult, clamp_lines
from system_controller.models.service import Action, Backend

# 「開機自動啟動」在 Docker 中對應容器的 restart policy
ENABLED_RESTART_POLICY = "unless-stopped"
DISABLED_RESTART_POLICY = "no"

ACTION_ARGS: dict[Action, tuple[str, ...]] = {
    Action.START: ("start",),
    Action.STOP: ("stop",),
    Action.RESTART: ("restart",),
    Action.ENABLE: ("update", f"--restart={ENABLED_RESTART_POLICY}"),
    Action.DISABLE: ("update", f"--restart={DISABLED_RESTART_POLICY}"),
}


class DockerAdapter(CommandAdapter):
    """透過掛載進容器的 /var/run/docker.sock 操作主機的 Docker。"""

    backend = Backend.DOCKER

    def __init__(self, docker_path: str = "/usr/bin/docker"):
        self.docker_path = docker_path

    async def _docker(self, *args: str, container: str, timeout: float = 30, merge_stderr: bool = False) -> CommandResult:
        if error := self._invalid_target(container): return error

        # "--" 確保容器名稱一定被解讀為參數而非選項
        return await self._run(self.docker_path, *args, "--", container, timeout=timeout, merge_stderr=merge_stderr)

    async def control(self, action: Action, container: str) -> CommandResult:
        return await self._docker(*ACTION_ARGS[Action(action)], container=container)

    async def state(self, container: str) -> BackendState:
        result = await self._docker(
            "inspect", "--type=container", "--format={{.State.Status}} {{.HostConfig.RestartPolicy.Name}}",
            container=container, timeout=10,
        )

        if not result.success:
            return BackendState(active="not-found", enabled="unknown")

        status, _, policy = result.stdout.strip().partition(" ")
        policy = policy or DISABLED_RESTART_POLICY

        return BackendState(
            active=status or "unknown",
            enabled="disabled" if policy == DISABLED_RESTART_POLICY else f"enabled ({policy})",
        )

    async def logs(self, container: str, lines: int = 50) -> CommandResult:
        # 容器的 stdout / stderr 都屬於日誌內容，合併輸出
        return await self._docker(
            "logs", f"--tail={clamp_lines(lines)}", "--timestamps",
            container=container, timeout=10, merge_stderr=True,
        )
