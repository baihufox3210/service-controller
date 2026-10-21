from types import SimpleNamespace

import pytest

from system_controller.core.permissions import GuildNotAllowed, PermissionDenied, PermissionPolicy, require
from system_controller.models.permission import Grant, Permission, SubjectType
from system_controller.repositories.permission_repository import PermissionRepository
from tests.conftest import GUILD_ID, make_interaction

OWNER, ADMIN, OPERATOR, WEB_OPERATOR, NOBODY = 1, 2, 3, 4, 9
VIEWER_ROLE = 30


@pytest.fixture
def policy(tmp_db):
    repository = PermissionRepository(tmp_db.permissions)
    repository.set(Grant(SubjectType.USER, ADMIN, Permission.ADMIN))
    repository.set(Grant(SubjectType.USER, OPERATOR, Permission.OPERATOR))
    repository.set(Grant(SubjectType.USER, WEB_OPERATOR, Permission.OPERATOR, "web"))
    repository.set(Grant(SubjectType.ROLE, VIEWER_ROLE, Permission.VIEWER))

    return PermissionPolicy(repository, owner_ids={OWNER}, allowed_guild_ids={GUILD_ID})


@pytest.mark.parametrize(
    ("user_id", "roles", "service", "expected"),
    [
        (OWNER, [], None, Permission.OWNER),
        (ADMIN, [], "web", Permission.ADMIN),
        (OPERATOR, [], None, Permission.OPERATOR),
        (WEB_OPERATOR, [], "web", Permission.OPERATOR),
        (WEB_OPERATOR, [], "db", Permission.NONE),
        (WEB_OPERATOR, [], None, Permission.NONE),
        (WEB_OPERATOR, [VIEWER_ROLE], "db", Permission.VIEWER),
        (NOBODY, [VIEWER_ROLE], None, Permission.VIEWER),
        (NOBODY, [], None, Permission.NONE),
    ],
)
def test_level_for(policy, user_id, roles, service, expected):
    assert policy.level_for(user_id, roles, GUILD_ID, service) is expected


@pytest.mark.parametrize("guild_id", [None, 200])
def test_disallowed_guild_or_dm(policy, guild_id):
    assert policy.level_for(OWNER, [], guild_id) is Permission.NONE


def predicate_of(level, per_service=False):
    @require(level, per_service=per_service)
    async def command(): ...

    return command.__discord_app_commands_checks__[0]


@pytest.mark.asyncio
async def test_require_uses_service_argument(policy):
    client = SimpleNamespace(permissions=policy)
    check = predicate_of(Permission.OPERATOR, per_service=True)

    assert await check(make_interaction(client, WEB_OPERATOR, service="web")) is True

    with pytest.raises(PermissionDenied):
        await check(make_interaction(client, WEB_OPERATOR, service="db"))


@pytest.mark.asyncio
async def test_require_global_ignores_service_grants(policy):
    with pytest.raises(PermissionDenied):
        await predicate_of(Permission.OPERATOR)(make_interaction(SimpleNamespace(permissions=policy), WEB_OPERATOR, service="web"))


@pytest.mark.asyncio
async def test_require_rejects_other_guild(policy):
    with pytest.raises(GuildNotAllowed):
        await predicate_of(Permission.VIEWER)(make_interaction(SimpleNamespace(permissions=policy), OWNER, guild_id=200))
