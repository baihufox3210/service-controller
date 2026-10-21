import discord
from discord import app_commands
from discord.ext import commands

from system_controller.core.interactions import audit_log, service_autocomplete
from system_controller.core.permissions import PermissionPolicy, get_policy, require
from system_controller.models.permission import Grant, Permission, SubjectType
from system_controller.services.system_service import SystemService

LEVEL_CHOICES = [
    app_commands.Choice(name="管理員（全域）", value="admin"),
    app_commands.Choice(name="操作者", value="operator"),
    app_commands.Choice(name="檢視者", value="viewer"),
]


def subject_of(target: discord.Member | discord.Role) -> tuple[SubjectType, int]:
    return (SubjectType.ROLE if isinstance(target, discord.Role) else SubjectType.USER), target.id


def required_to_manage(level: Permission) -> Permission:
    # 管理員只能由擁有者新增或移除；其餘授權由管理員管理
    return Permission.OWNER if level >= Permission.ADMIN else Permission.ADMIN


@app_commands.guild_only()
class Access(commands.GroupCog, group_name="access", group_description="權限管理"):
    def __init__(self, bot: commands.Bot, policy: PermissionPolicy, system_service: SystemService):
        self.bot = bot
        self.policy = policy
        self.system_service = system_service
        super().__init__()

    def _ensure_can_manage(self, interaction: discord.Interaction, *levels: Permission) -> None:
        required = max(required_to_manage(level) for level in levels)
        self.policy.ensure(interaction, required)

    def _check_service(self, service: str | None) -> str | None:
        if service is not None and self.system_service.get(service) is None:
            return f"❌ 找不到服務 **{service}**"

        return None

    @app_commands.command(name="grant", description="授予使用者或身分組權限（指定 service 則只對該服務有效）")
    @app_commands.describe(target="使用者或身分組", level="權限等級", service="只對此服務授權（留空為全域）")
    @app_commands.choices(level=LEVEL_CHOICES)
    @app_commands.autocomplete(service=service_autocomplete)
    @require(Permission.ADMIN)
    async def grant(
        self,
        interaction: discord.Interaction,
        target: discord.Member | discord.Role,
        level: str,
        service: str | None = None,
    ):
        if error := self._check_service(service):
            await interaction.response.send_message(error, ephemeral=True)
            return

        subject_type, subject_id = subject_of(target)
        new_level = Permission[level.upper()]
        existing = self.policy.repository.get(subject_type, subject_id, service)

        # 覆寫既有授權也需要足以管理該授權的權限（例如把管理員降級）
        self._ensure_can_manage(interaction, new_level, *([existing.level] if existing else []))

        try:
            grant = Grant(subject_type, subject_id, new_level, service)

        except ValueError as e:
            await interaction.response.send_message(f"❌ {e}", ephemeral=True)
            return

        self.policy.repository.set(grant)
        audit_log(interaction, "grant", f"{subject_type}:{subject_id}", True, f"level={level} service={service}")
        await interaction.response.send_message(
            f"✅ 已授予 {grant.mention} **{new_level.label}** 權限" + (f"（服務 **{service}**）" if service else "（全域）")
        )

    @app_commands.command(name="revoke", description="撤銷使用者或身分組的權限")
    @app_commands.describe(target="使用者或身分組", service="撤銷此服務的單獨授權（留空為全域授權）")
    @app_commands.autocomplete(service=service_autocomplete)
    @require(Permission.ADMIN)
    async def revoke(self, interaction: discord.Interaction, target: discord.Member | discord.Role, service: str | None = None):
        subject_type, subject_id = subject_of(target)
        existing = self.policy.repository.get(subject_type, subject_id, service)

        if existing is None:
            await interaction.response.send_message(f"❌ {target.mention} 沒有這項授權。", ephemeral=True)
            return

        self._ensure_can_manage(interaction, existing.level)
        self.policy.repository.remove(subject_type, subject_id, service)
        audit_log(interaction, "revoke", f"{subject_type}:{subject_id}", True, f"level={existing.level.name} service={service}")
        await interaction.response.send_message(f"✅ 已撤銷 {target.mention} 的 **{existing.level.label}** 權限")

    @app_commands.command(name="list", description="列出權限設定")
    @app_commands.describe(service="只顯示此服務的單獨授權")
    @app_commands.autocomplete(service=service_autocomplete)
    @require(Permission.ADMIN)
    async def list_grants(self, interaction: discord.Interaction, service: str | None = None):
        lines = []

        if service is None:
            owners = "、".join(f"<@{owner_id}>" for owner_id in sorted(self.policy.owner_ids)) or "（未設定）"
            lines.append(f"**擁有者：** {owners}")

        grants = sorted(self.policy.repository.find(service), key=lambda g: (g.service or "", -g.level))

        for grant in grants:
            scope = f"服務 **{grant.service}**" if grant.service else "全域"
            lines.append(f"- {grant.mention}：**{grant.level.label}**（{scope}）")

        if len(lines) <= (0 if service else 1):
            lines.append("目前沒有任何授權。")

        await interaction.response.send_message("\n".join(lines)[:1900], ephemeral=True)

    @app_commands.command(name="whoami", description="查看自己的權限")
    @app_commands.describe(service="查看對此服務的權限")
    @app_commands.autocomplete(service=service_autocomplete)
    async def whoami(self, interaction: discord.Interaction, service: str | None = None):
        level = get_policy(interaction).level_for_interaction(interaction, service)
        scope = f"（服務 **{service}**）" if service else "（全域）"
        await interaction.response.send_message(f"你目前的權限：**{level.label}**{scope}", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Access(bot, bot.permissions, bot.system_service))

