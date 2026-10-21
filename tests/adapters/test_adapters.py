from unittest.mock import AsyncMock

import pytest

from system_controller.adapters.base import CommandAdapter
from system_controller.adapters.docker import DockerAdapter
from system_controller.adapters.systemd import SystemdAdapter
from system_controller.models.service import Action
from tests.conftest import fail, ok


@pytest.fixture
def systemd():
    adapter = SystemdAdapter()
    adapter._run = AsyncMock(return_value=ok())
    return adapter


@pytest.fixture
def docker():
    adapter = DockerAdapter()
    adapter._run = AsyncMock(return_value=ok())
    return adapter


@pytest.mark.asyncio
@pytest.mark.parametrize("action", list(Action))
async def test_systemd_control_uses_sudo_and_separator(systemd, action):
    await systemd.control(action, "nginx.service")

    assert systemd._run.await_args.args == (
        "/usr/bin/sudo", "-n", "/usr/bin/systemctl", action.value, "--", "nginx.service",
    )


@pytest.mark.asyncio
async def test_systemd_state_does_not_use_sudo(systemd):
    systemd._run = AsyncMock(side_effect=[ok("active\n"), ok("enabled\n")])

    state = await systemd.state("nginx.service")

    assert (state.active, state.enabled) == ("active", "enabled")
    assert [call.args[0] for call in systemd._run.await_args_list] == ["/usr/bin/systemctl"] * 2


@pytest.mark.asyncio
async def test_systemd_logs_clamps_lines(systemd):
    await systemd.logs("nginx.service", lines=100000)

    args = systemd._run.await_args.args
    assert args[:2] == ("/usr/bin/journalctl", "--unit=nginx.service")
    assert args[args.index("-n") + 1] == "500"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "args"),
    [
        (Action.START, ("start",)),
        (Action.STOP, ("stop",)),
        (Action.RESTART, ("restart",)),
        (Action.ENABLE, ("update", "--restart=unless-stopped")),
        (Action.DISABLE, ("update", "--restart=no")),
    ],
)
async def test_docker_control(docker, action, args):
    await docker.control(action, "my-web")

    assert docker._run.await_args.args == ("/usr/bin/sudo", "-n", "/usr/bin/docker", *args, "--", "my-web")


@pytest.mark.asyncio
async def test_docker_without_sudo():
    adapter = DockerAdapter(use_sudo=False)
    adapter._run = AsyncMock(return_value=ok())

    await adapter.control(Action.STOP, "db")

    assert adapter._run.await_args.args == ("/usr/bin/docker", "stop", "--", "db")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("result", "active", "enabled"),
    [
        (ok("running unless-stopped\n"), "running", "enabled (unless-stopped)"),
        (ok("exited no\n"), "exited", "disabled"),
        (fail("No such container"), "not-found", "unknown"),
    ],
)
async def test_docker_state(docker, result, active, enabled):
    docker._run = AsyncMock(return_value=result)

    state = await docker.state("my-web")

    assert (state.active, state.enabled) == (active, enabled)


@pytest.mark.asyncio
async def test_docker_logs_merges_stderr(docker):
    await docker.logs("my-web", lines=20)

    assert docker._run.await_args.args[3:] == ("logs", "--tail=20", "--timestamps", "--", "my-web")
    assert docker._run.await_args.kwargs["merge_stderr"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(("adapter_type", "target"), [(SystemdAdapter, "--help"), (DockerAdapter, "-v")])
async def test_rejects_invalid_target(adapter_type, target):
    adapter = adapter_type()
    adapter._run = AsyncMock(return_value=ok())

    result = await adapter.control(Action.START, target)

    assert result.success is False
    adapter._run.assert_not_called()


# 真正執行子程序

@pytest.mark.asyncio
async def test_run_real_process():
    result = await CommandAdapter()._run("sh", "-c", "echo out; echo err >&2; exit 3")

    assert (result.code, result.stdout, result.stderr) == (3, "out\n", "err\n")


@pytest.mark.asyncio
async def test_run_timeout_kills_process():
    result = await CommandAdapter()._run("sleep", "10", timeout=0.2)

    assert (result.success, result.stderr) == (False, "Command timed out.")


@pytest.mark.asyncio
async def test_run_missing_binary():
    assert (await CommandAdapter()._run("/nonexistent/binary")).success is False
