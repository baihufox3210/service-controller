from types import SimpleNamespace

import pytest

from system_controller.core.config import Settings
from system_controller.core.permissions import (
    GuildNotAllowed,
    Permission,
    PermissionDenied,
    PermissionPolicy,
    require,
)

GUILD = 100
OTHER_GUILD = 200


@pytest.fixture
def settings():
    return Settings(
        token="x",
        allowed_guild_ids=frozenset({GUILD}),
        admin_user_ids=frozenset({1}),
        admin_role_ids=frozenset({11}),
        operator_user_ids=frozenset({2}),
        operator_role_ids=frozenset({22}),
        viewer_user_ids=frozenset({3}),
        viewer_role_ids=frozenset({33}),
    )


@pytest.fixture
def policy(settings):
    return PermissionPolicy(settings)


@pytest.mark.parametrize(
    ("user_id", "roles", "expected"),
    [
        (1, [], Permission.ADMIN),
        (9, [11], Permission.ADMIN),
        (2, [], Permission.OPERATOR),
        (9, [22], Permission.OPERATOR),
        (3, [], Permission.VIEWER),
        (9, [33], Permission.VIEWER),
        (9, [], Permission.NONE),
        (3, [11], Permission.ADMIN),  # 取最高權限
    ],
)
def test_level_for(policy, user_id, roles, expected):
    assert policy.level_for(user_id, roles, GUILD) is expected


def test_disallowed_guild_has_no_permission(policy):
    assert policy.level_for(1, [11], OTHER_GUILD) is Permission.NONE


def test_dm_has_no_permission(policy):
    assert policy.level_for(1, [], None) is Permission.NONE


def test_empty_allowlist_allows_any_guild():
    policy = PermissionPolicy(Settings(token="x", admin_user_ids=frozenset({1})))

    assert policy.level_for(1, [], OTHER_GUILD) is Permission.ADMIN


def test_default_deny():
    policy = PermissionPolicy(Settings(token="x"))

    assert policy.level_for(1, [11], GUILD) is Permission.NONE


def interaction(policy, user_id, role_ids=(), guild_id=GUILD):
    return SimpleNamespace(
        client=SimpleNamespace(permissions=policy),
        user=SimpleNamespace(id=user_id, roles=[SimpleNamespace(id=r) for r in role_ids]),
        guild_id=guild_id,
    )


def predicate_of(level):
    @require(level)
    async def command(): ...

    return command.__discord_app_commands_checks__[0]


@pytest.mark.asyncio
async def test_require_allows_sufficient_level(policy):
    assert await predicate_of(Permission.OPERATOR)(interaction(policy, 1)) is True


@pytest.mark.asyncio
async def test_require_denies_insufficient_level(policy):
    with pytest.raises(PermissionDenied):
        await predicate_of(Permission.OPERATOR)(interaction(policy, 3))


@pytest.mark.asyncio
async def test_require_denies_other_guild(policy):
    with pytest.raises(GuildNotAllowed):
        await predicate_of(Permission.VIEWER)(interaction(policy, 1, guild_id=OTHER_GUILD))
