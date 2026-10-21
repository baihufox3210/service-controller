from collections.abc import Iterable
from enum import IntEnum

import discord
from discord import app_commands

from system_controller.core.config import Settings


class Permission(IntEnum):
    NONE = 0
    VIEWER = 1      # list / status / logs
    OPERATOR = 2    # start / stop / restart / enable / disable
    ADMIN = 3       # add / remove

    @property
    def label(self) -> str:
        return {
            Permission.NONE: "無",
            Permission.VIEWER: "檢視者",
            Permission.OPERATOR: "操作者",
            Permission.ADMIN: "管理員",
        }[self]


class PermissionDenied(app_commands.CheckFailure):
    def __init__(self, required: Permission, actual: Permission):
        self.required = required
        self.actual = actual
        super().__init__(f"需要「{required.label}」權限，你目前的權限為「{actual.label}」。")


class GuildNotAllowed(app_commands.CheckFailure):
    def __init__(self):
        super().__init__("此伺服器未被授權使用本機器人。")


class PermissionPolicy:
    """預設拒絕：未列在設定中的使用者 / 身分組沒有任何權限。"""

    def __init__(self, settings: Settings):
        self.allowed_guild_ids = settings.allowed_guild_ids
        self._users = {
            Permission.ADMIN: settings.admin_user_ids,
            Permission.OPERATOR: settings.operator_user_ids,
            Permission.VIEWER: settings.viewer_user_ids,
        }
        self._roles = {
            Permission.ADMIN: settings.admin_role_ids,
            Permission.OPERATOR: settings.operator_role_ids,
            Permission.VIEWER: settings.viewer_role_ids,
        }

    def guild_allowed(self, guild_id: int | None) -> bool:
        if guild_id is None: return False
        if not self.allowed_guild_ids: return True

        return guild_id in self.allowed_guild_ids

    def level_for(self, user_id: int, role_ids: Iterable[int], guild_id: int | None) -> Permission:
        if not self.guild_allowed(guild_id): return Permission.NONE

        role_ids = set(role_ids)

        for level in (Permission.ADMIN, Permission.OPERATOR, Permission.VIEWER):
            if user_id in self._users[level] or role_ids & self._roles[level]:
                return level

        return Permission.NONE

    def level_for_interaction(self, interaction: discord.Interaction) -> Permission:
        user = interaction.user
        role_ids = [role.id for role in getattr(user, "roles", [])]

        return self.level_for(user.id, role_ids, interaction.guild_id)


def get_policy(interaction: discord.Interaction) -> PermissionPolicy:
    policy = getattr(interaction.client, "permissions", None)

    if not isinstance(policy, PermissionPolicy):
        raise app_commands.CheckFailure("權限設定尚未初始化。")

    return policy


def require(level: Permission):
    async def predicate(interaction: discord.Interaction) -> bool:
        policy = get_policy(interaction)

        if not policy.guild_allowed(interaction.guild_id):
            raise GuildNotAllowed()

        actual = policy.level_for_interaction(interaction)

        if actual < level:
            raise PermissionDenied(level, actual)

        return True

    return app_commands.check(predicate)
