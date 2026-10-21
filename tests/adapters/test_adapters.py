from unittest.mock import AsyncMock

import pytest

from system_controller.adapters.base import CommandAdapter, CommandResult
from system_controller.adapters.docker import DockerAdapter
from system_controller.adapters.systemd import SystemdAdapter


def ok(stdout=""):
    return CommandResult(code=0, stdout=stdout, stderr="")


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
async def test_systemd_control_uses_sudo_and_separator(systemd):
    await systemd.restart("nginx.service")

    assert systemd._run.await_args.args == (
        "/usr/bin/sudo", "-n", "/usr/bin/systemctl", "restart", "--", "nginx.service",
    )


@pytest.mark.asyncio
async def test_systemd_queries_do_not_use_sudo(systemd):
    systemd._run = AsyncMock(side_effect=[ok("active\n"), ok("enabled\n")])

    state = await systemd.state("nginx.service")

    assert (state.active, state.enabled) == ("active", "enabled")
    assert systemd._run.await_args_list[0].args[0] == "/usr/bin/systemctl"


@pytest.mark.asyncio
async def test_systemd_logs_clamps_lines(systemd):
    await systemd.logs("nginx.service", lines=100000)

    args = systemd._run.await_args.args
    assert args[0] == "/usr/bin/journalctl"
    assert "--unit=nginx.service" in args
    assert args[args.index("-n") + 1] == "500"


@pytest.mark.asyncio
@pytest.mark.parametrize("unit", ["--help", "-H", "nginx", "a b.service", "../x.service"])
async def test_systemd_rejects_invalid_unit(systemd, unit):
    result = await systemd.start(unit)

    assert result.success is False
    systemd._run.assert_not_called()


@pytest.mark.asyncio
async def test_docker_control_commands(docker):
    await docker.start("my-web")
    assert docker._run.await_args.args == ("/usr/bin/sudo", "-n", "/usr/bin/docker", "start", "--", "my-web")

    await docker.enable("my-web")
    assert docker._run.await_args.args[3:] == ("update", "--restart=unless-stopped", "--", "my-web")

    await docker.disable("my-web")
    assert docker._run.await_args.args[3:] == ("update", "--restart=no", "--", "my-web")


@pytest.mark.asyncio
async def test_docker_without_sudo():
    adapter = DockerAdapter(use_sudo=False)
    adapter._run = AsyncMock(return_value=ok())

    await adapter.stop("db")

    assert adapter._run.await_args.args == ("/usr/bin/docker", "stop", "--", "db")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stdout", "active", "enabled"),
    [
        ("running unless-stopped\n", "running", "enabled (unless-stopped)"),
        ("exited no\n", "exited", "disabled"),
        ("created \n", "created", "disabled"),
    ],
)
async def test_docker_state(docker, stdout, active, enabled):
    docker._run = AsyncMock(return_value=ok(stdout))

    state = await docker.state("my-web")

    assert (state.active, state.enabled) == (active, enabled)


@pytest.mark.asyncio
async def test_docker_state_missing_container(docker):
    docker._run = AsyncMock(return_value=CommandResult(code=1, stdout="", stderr="No such container"))

    state = await docker.state("ghost")

    assert state.active == "not-found"


@pytest.mark.asyncio
async def test_docker_logs_merges_stderr(docker):
    await docker.logs("my-web", lines=20)

    assert docker._run.await_args.args[3:] == ("logs", "--tail=20", "--timestamps", "--", "my-web")
    assert docker._run.await_args.kwargs["merge_stderr"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["-v", "--rm", "a/b", "a b", ""])
async def test_docker_rejects_invalid_container(docker, name):
    result = await docker.restart(name)

    assert result.success is False
    docker._run.assert_not_called()


# 真正執行子程序的測試

@pytest.mark.asyncio
async def test_run_real_process():
    result = await CommandAdapter()._run("sh", "-c", "echo out; echo err >&2; exit 3")

    assert (result.code, result.stdout, result.stderr) == (3, "out\n", "err\n")


@pytest.mark.asyncio
async def test_run_timeout_kills_process():
    result = await CommandAdapter()._run("sleep", "10", timeout=0.2)

    assert result.success is False
    assert result.stderr == "Command timed out."


@pytest.mark.asyncio
async def test_run_missing_binary():
    result = await CommandAdapter()._run("/nonexistent/binary")

    assert result.success is False
