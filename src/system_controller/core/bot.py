import logging
import pkgutil

import discord
from discord import app_commands
from discord.ext import commands

from system_controller import cogs
from system_controller.adapters.docker import DockerAdapter
from system_controller.adapters.systemd import SystemdAdapter
from system_controller.core.config import Settings
from system_controller.core.permissions import PermissionPolicy
from system_controller.repositories.repository import ServiceRepository
from system_controller.services.system_service import SystemService

logger = logging.getLogger(__name__)


class Bot(commands.Bot):
    def __init__(self, settings: Settings):
        # 斜線指令不需要任何 privileged intent
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=discord.Intents.default(),
            allowed_mentions=discord.AllowedMentions.none(),
            help_command=None,
        )

        self.settings = settings
        self.permissions = PermissionPolicy(settings)
        self.system_service = SystemService(
            repository=ServiceRepository(settings.services_file),
            systemd=SystemdAdapter(
                sudo_path=settings.sudo_path,
                systemctl_path=settings.systemctl_path,
                journalctl_path=settings.journalctl_path,
            ),
            docker=DockerAdapter(
                docker_path=settings.docker_path,
                sudo_path=settings.sudo_path,
                use_sudo=settings.docker_use_sudo,
            ),
        )

        self.tree.error(self.on_app_command_error)

    async def setup_hook(self):
        await self.load_extensions()
        await self.tree.sync()

    async def load_extensions(self):
        for module in pkgutil.iter_modules(cogs.__path__, f"{cogs.__name__}."):
            try:
                await self.load_extension(module.name)
                logger.info("已載入擴充：%s", module.name)

            except Exception:
                logger.exception("擴充載入失敗：%s", module.name)

    async def on_ready(self):
        logger.info("已登入為 %s (%s)", self.user, self.user.id if self.user else "?")

    async def on_app_command_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CommandOnCooldown):
            message = f"指令冷卻中，請在 {error.retry_after:.1f} 秒後再試。"
        elif isinstance(error, app_commands.NoPrivateMessage):
            message = "此指令只能在伺服器中使用。"
        elif isinstance(error, app_commands.CheckFailure):
            logger.warning(
                "權限拒絕 user=%s(%s) guild=%s command=%s reason=%s",
                interaction.user, interaction.user.id, interaction.guild_id,
                interaction.command.qualified_name if interaction.command else "?", error,
            )
            message = str(error) or "你沒有權限執行此指令。"
        else:
            logger.error("指令執行錯誤", exc_info=error)
            message = "執行指令時發生內部錯誤，請聯絡管理員查看日誌。"

        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
