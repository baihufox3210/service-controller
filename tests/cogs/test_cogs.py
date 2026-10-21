from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest
from discord.ext import commands

from system_controller.cogs.access import Access
from system_controller.cogs.system import Services
from system_controller.core import embeds
from system_controller.core.interactions import code_block
from system_controller.core.permissions import PermissionDenied, PermissionPolicy
from system_controller.models.permission import Grant, Permission, SubjectType
from system_controller.models.service import Backend
from system_controller.repositories.permission_repository import PermissionRepository
from system_controller.repositories.service_repository import ServiceRepository
from system_controller.services.system_service import SystemService
from tests.conftest import fail, make_interaction, ok, sent_text

OWNER, ADMIN, OTHER_ADMIN, USER = 1, 2, 3, 4


@pytest.fixture
def client(tmp_db):
    repository = PermissionRepository(tmp_db.permissions)
    repository.set(Grant(SubjectType.USER, ADMIN, Permission.ADMIN))
    repository.set(Grant(SubjectType.USER, OTHER_ADMIN, Permission.ADMIN))

    backends = {backend: Mock(control=AsyncMock(return_value=ok())) for backend in Backend}
    system_service = SystemService(ServiceRepository(tmp_db.services), backends)
    system_service.add("web", {Backend.SYSTEMD: "web.service", Backend.DOCKER: "web"})

    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    bot.permissions = PermissionPolicy(repository, owner_ids={OWNER})
    bot.system_service = system_service

    return bot


@pytest.fixture
def access(client):
    return Access(client, client.permissions, client.system_service)


@pytest.fixture
def services(client):
    return Services(client, client.system_service)


def member(user_id):
    return SimpleNamespace(id=user_id, mention=f"<@{user_id}>")


def role(role_id):
    return Mock(spec=discord.Role, id=role_id, mention=f"<@&{role_id}>")


def reply(interaction):
    return sent_text(interaction.response.send_message)


def test_commands_are_guarded(services, access):
    for group, unguarded in ((services.app_command, {"list"}), (access.app_command, {"whoami"})):
        assert group.guild_only is True

        for command in group.commands:
            assert bool(command.checks) != (command.name in unguarded), command.name


@pytest.mark.asyncio
async def test_owner_can_grant_admin(client, access):
    interaction = make_interaction(client, OWNER)

    await access.grant.callback(access, interaction, member(USER), "admin")

    assert client.permissions.level_for(USER, [], 100) is Permission.ADMIN


@pytest.mark.asyncio
async def test_admin_cannot_grant_or_revoke_admin(client, access):
    with pytest.raises(PermissionDenied):
        await access.grant.callback(access, make_interaction(client, ADMIN), member(USER), "admin")

    with pytest.raises(PermissionDenied):
        await access.revoke.callback(access, make_interaction(client, ADMIN), member(OTHER_ADMIN))

    # 也不能透過覆寫把其他管理員降級
    with pytest.raises(PermissionDenied):
        await access.grant.callback(access, make_interaction(client, ADMIN), member(OTHER_ADMIN), "viewer")

    assert client.permissions.level_for(OTHER_ADMIN, [], 100) is Permission.ADMIN


@pytest.mark.asyncio
async def test_admin_grants_per_service_operator_to_role(client, access):
    await access.grant.callback(access, make_interaction(client, ADMIN), role(50), "operator", "web")

    assert client.permissions.level_for(USER, [50], 100, "web") is Permission.OPERATOR
    assert client.permissions.level_for(USER, [50], 100) is Permission.NONE

    await access.revoke.callback(access, make_interaction(client, ADMIN), role(50), "web")

    assert client.permissions.level_for(USER, [50], 100, "web") is Permission.NONE


@pytest.mark.asyncio
async def test_grant_validation(client, access):
    interaction = make_interaction(client, OWNER)
    await access.grant.callback(access, interaction, member(USER), "admin", "web")
    assert "無法授予" in reply(interaction)

    interaction = make_interaction(client, OWNER)
    await access.grant.callback(access, interaction, member(USER), "viewer", "ghost")
    assert "找不到服務" in reply(interaction)

    assert client.permissions.level_for(USER, [], 100, "web") is Permission.NONE


@pytest.mark.asyncio
async def test_control_output_lists_each_backend(client, services):
    client.system_service.backends[Backend.DOCKER].control.return_value = fail("No such container")
    interaction = make_interaction(client, OWNER)

    await services.restart.callback(services, interaction, "web", "all")

    embed = interaction.followup.send.await_args.kwargs["embed"]
    assert embed.title == "部分重啟失敗"
    assert embed.color == embeds.Color.WARNING
    assert [field.name for field in embed.fields] == ["systemd · web.service", "docker · web"]
    assert embed.fields[0].value == "成功"
    assert "No such container" in embed.fields[1].value


@pytest.mark.asyncio
async def test_remove_service_also_removes_its_grants(client, services):
    client.permissions.repository.set(Grant(SubjectType.USER, USER, Permission.OPERATOR, "web"))

    await services.remove.callback(services, make_interaction(client, ADMIN), "web")

    assert client.permissions.repository.find("web") == []


@pytest.mark.asyncio
async def test_list_only_shows_visible_services(client, services):
    client.system_service.add("db", {Backend.DOCKER: "postgres"})
    client.permissions.repository.set(Grant(SubjectType.USER, USER, Permission.VIEWER, "db"))
    interaction = make_interaction(client, USER)

    await services.service_list.callback(services, interaction)

    assert "db" in reply(interaction) and "web" not in reply(interaction)


@pytest.mark.asyncio
async def test_logs_shows_each_backend(client, services):
    backends = client.system_service.backends
    backends[Backend.SYSTEMD].logs = AsyncMock(return_value=ok("api started\n"))
    backends[Backend.DOCKER].logs = AsyncMock(return_value=fail("No such container"))
    interaction = make_interaction(client, OWNER)

    await services.logs.callback(services, interaction, "web", "all", 50)

    kwargs = interaction.followup.send.await_args.kwargs
    assert kwargs["ephemeral"] is True
    assert kwargs["files"] == []
    assert [embed.author.name for embed in kwargs["embeds"]] == ["systemd · web.service", "docker · web"]
    assert "api started" in kwargs["embeds"][0].description
    assert "No such container" in kwargs["embeds"][1].description


@pytest.mark.asyncio
async def test_long_logs_keep_latest_lines_and_attach_file(client, services):
    content = "\n".join(f"line {i}" for i in range(2000))
    client.system_service.backends[Backend.SYSTEMD].logs = AsyncMock(return_value=ok(content))
    interaction = make_interaction(client, OWNER)

    await services.logs.callback(services, interaction, "web", "systemd", 500)

    kwargs = interaction.followup.send.await_args.kwargs
    description = kwargs["embeds"][0].description

    assert len(description) <= 4096
    assert "line 1999" in description and "line 0\n" not in description
    assert len(kwargs["files"]) == 1 and kwargs["files"][0].filename == "web_logs.txt"


@pytest.mark.asyncio
async def test_status_embed(client, services):
    from system_controller.adapters.base import BackendState

    backends = client.system_service.backends
    backends[Backend.SYSTEMD].state = AsyncMock(return_value=BackendState("active", "enabled"))
    backends[Backend.DOCKER].state = AsyncMock(return_value=BackendState("exited", "disabled"))
    interaction = make_interaction(client, OWNER)

    await services.status.callback(services, interaction, "web", "all")

    embed = interaction.followup.send.await_args.kwargs["embed"]
    assert embed.color == embeds.Color.WARNING  # 有一個後端已停止
    assert "運作中**（`active`）" in embed.fields[0].value
    assert "已停止**（`exited`）" in embed.fields[1].value


@pytest.mark.asyncio
async def test_add_without_suffix_defaults_to_service(client, services):
    interaction = make_interaction(client, OWNER)

    await services.add.callback(services, interaction, "blog", "baihu-blog-api", None)

    assert client.system_service.get("blog").targets == {Backend.SYSTEMD: "baihu-blog-api.service"}
    assert "`baihu-blog-api.service`" in reply(interaction)


@pytest.mark.asyncio
async def test_status_shows_query_error(client, services):
    from system_controller.adapters.base import BackendState

    backends = client.system_service.backends
    backends[Backend.SYSTEMD].state = AsyncMock(return_value=BackendState("unknown", "unknown", "Access denied"))
    backends[Backend.DOCKER].state = AsyncMock(return_value=BackendState("running", "disabled"))
    interaction = make_interaction(client, OWNER)

    await services.status.callback(services, interaction, "web", "all")

    embed = interaction.followup.send.await_args.kwargs["embed"]
    assert embed.color == embeds.Color.FAILURE
    assert "無法查詢狀態" in embed.fields[0].value and "Access denied" in embed.fields[0].value


def test_code_block_cannot_be_escaped():
    assert code_block("a```b") == "```\na`​``b\n```"
