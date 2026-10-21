from collections.abc import Iterable

import discord
from discord import app_commands

from system_controller.models.permission import Permission
from system_controller.repositories.permission_repository import PermissionRepository


class PermissionDenied(app_commands.CheckFailure):
    def __init__(self, required: Permission, actual: Permission):
        self.required = required
        self.actual = actual
        super().__init__(f"需要「{required.label}」權限，你目前的權限為「{actual.label}」。")


class GuildNotAllowed(app_commands.CheckFailure):
    def __init__(self):
        super().__init__("此伺服器未被授權使用本機器人。")


class PermissionPolicy:
    """預設拒絕：擁有者來自設定檔，其餘權限來自 PermissionRepository 的授權。"""

    def __init__(self, repository: PermissionRepository, owner_ids: Iterable[int] = (), allowed_guild_ids: Iterable[int] = ()):
        self.repository = repository
        self.owner_ids = set(owner_ids)
        self.allowed_guild_ids = frozenset(allowed_guild_ids)

    def guild_allowed(self, guild_id: int | None) -> bool:
        if guild_id is None: return False

        return not self.allowed_guild_ids or guild_id in self.allowed_guild_ids

    def level_for(self, user_id: int, role_ids: Iterable[int], guild_id: int | None, service: str | None = None) -> Permission:
        """service 有值時，會一併套用該服務的單獨授權；取最高權限。"""
        if not self.guild_allowed(guild_id): return Permission.NONE
        if user_id in self.owner_ids: return Permission.OWNER

        grants = self.repository.matching(user_id, role_ids, service)

        return max((grant.level for grant in grants), default=Permission.NONE)

    def level_for_interaction(self, interaction: discord.Interaction, service: str | None = None) -> Permission:
        role_ids = [role.id for role in getattr(interaction.user, "roles", [])]

        return self.level_for(interaction.user.id, role_ids, interaction.guild_id, service)

    def ensure(self, interaction: discord.Interaction, level: Permission, service: str | None = None) -> Permission:
        if not self.guild_allowed(interaction.guild_id):
            raise GuildNotAllowed()

        actual = self.level_for_interaction(interaction, service)

        if actual < level:
            raise PermissionDenied(level, actual)

        return actual


def get_policy(interaction: discord.Interaction) -> PermissionPolicy:
    policy = getattr(interaction.client, "permissions", None)

    if not isinstance(policy, PermissionPolicy):
        raise app_commands.CheckFailure("權限設定尚未初始化。")

    return policy


def require(level: Permission, *, per_service: bool = False):
    """per_service=True 時，以指令的 service 參數套用單一服務授權。"""
    async def predicate(interaction: discord.Interaction) -> bool:
        service = getattr(interaction.namespace, "service", None) if per_service else None
        get_policy(interaction).ensure(interaction, level, service)

        return True

    return app_commands.check(predicate)
