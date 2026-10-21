from unittest.mock import AsyncMock, Mock

import pytest

from system_controller.adapters.base import BackendState
from system_controller.models.service import Action, Backend, InvalidServiceError
from system_controller.repositories.service_repository import ServiceRepository
from system_controller.services.system_service import ServiceNotFound, SystemService
from tests.conftest import fail, ok


@pytest.fixture
def backends():
    return {backend: Mock(control=AsyncMock(return_value=ok())) for backend in Backend}


@pytest.fixture
def system_service(tmp_db, backends):
    service = SystemService(ServiceRepository(tmp_db.services), backends)
    service.add("web", {Backend.SYSTEMD: "web.service", Backend.DOCKER: "web"})
    service.add("db", {Backend.DOCKER: "postgres"})

    return service


def calls(backends):
    """依呼叫順序回傳 (backend, action, target)。"""
    order = []

    for backend, mock in backends.items():
        mock.control.side_effect = lambda action, target, backend=backend: order.append((backend, action, target)) or ok()

    return order


@pytest.mark.asyncio
async def test_control_all_backends_in_order(system_service, backends):
    order = calls(backends)

    results = await system_service.control("web", Action.START)

    assert [r.success for r in results] == [True, True]
    assert order == [(Backend.SYSTEMD, Action.START, "web.service"), (Backend.DOCKER, Action.START, "web")]


@pytest.mark.asyncio
async def test_stop_runs_in_reverse_order(system_service, backends):
    order = calls(backends)

    await system_service.control("web", Action.STOP)

    assert [backend for backend, *_ in order] == [Backend.DOCKER, Backend.SYSTEMD]


@pytest.mark.asyncio
async def test_control_single_backend(system_service, backends):
    await system_service.control("web", Action.RESTART, Backend.DOCKER)

    backends[Backend.DOCKER].control.assert_awaited_once_with(Action.RESTART, "web")
    backends[Backend.SYSTEMD].control.assert_not_called()


@pytest.mark.asyncio
async def test_control_reports_failure(system_service, backends):
    backends[Backend.SYSTEMD].control.return_value = fail("Unit not found.")

    results = await system_service.control("web", Action.START)

    assert [(r.backend, r.success, r.message) for r in results] == [
        (Backend.SYSTEMD, False, "Unit not found."),
        (Backend.DOCKER, True, ""),
    ]


@pytest.mark.asyncio
async def test_control_errors(system_service):
    with pytest.raises(ServiceNotFound):
        await system_service.control("ghost", Action.START)

    with pytest.raises(InvalidServiceError):
        await system_service.control("db", Action.START, Backend.SYSTEMD)


@pytest.mark.asyncio
async def test_status_and_logs(system_service, backends):
    backends[Backend.SYSTEMD].state = AsyncMock(return_value=BackendState("active", "enabled"))
    backends[Backend.DOCKER].state = AsyncMock(return_value=BackendState("running", "disabled"))
    backends[Backend.SYSTEMD].logs = AsyncMock(return_value=ok("a\n"))
    backends[Backend.DOCKER].logs = AsyncMock(return_value=fail("daemon down"))

    statuses = await system_service.status("web")
    logs = await system_service.logs("web", lines=10)

    assert [(s.backend, s.active) for s in statuses] == [(Backend.SYSTEMD, "active"), (Backend.DOCKER, "running")]
    assert [(log.success, log.message) for log in logs] == [(True, "a"), (False, "daemon down")]
    backends[Backend.DOCKER].logs.assert_awaited_once_with("web", 10)


def test_set_and_unset_target(system_service):
    assert system_service.set_target("db", Backend.SYSTEMD, "postgresql.service").targets == {
        Backend.SYSTEMD: "postgresql.service", Backend.DOCKER: "postgres",
    }
    assert system_service.unset_target("db", Backend.DOCKER).targets == {Backend.SYSTEMD: "postgresql.service"}

    with pytest.raises(InvalidServiceError):
        system_service.unset_target("db", Backend.SYSTEMD)  # 不能移除最後一個後端

    with pytest.raises(InvalidServiceError):
        system_service.set_target("db", Backend.DOCKER, "--privileged")

    with pytest.raises(ServiceNotFound):
        system_service.set_target("ghost", Backend.DOCKER, "x")


def test_add_and_remove(system_service):
    assert system_service.add("web", {Backend.DOCKER: "x"}) is False

    with pytest.raises(InvalidServiceError):
        system_service.add("new", {})

    assert system_service.remove("web") is True
    assert [s.name for s in system_service.get_all()] == ["db"]
