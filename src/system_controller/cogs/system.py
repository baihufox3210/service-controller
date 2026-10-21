import io

import discord
from discord import app_commands
from discord.ext import commands

from system_controller.core import embeds
from system_controller.core.interactions import audit_log, service_autocomplete, visible_services
from system_controller.core.permissions import require
from system_controller.models.permission import Permission
from system_controller.models.service import Action, Backend, InvalidServiceError
from system_controller.services.system_service import ServiceNotFound, SystemService

ACTION_LABELS = {
    Action.START: "啟動",
    Action.STOP: "關閉",
    Action.RESTART: "重啟",
    Action.ENABLE: "設定開機自動啟動",
    Action.DISABLE: "取消開機自動啟動",
}

BACKEND_CHOICES = [
    app_commands.Choice(name="systemd", value=Backend.SYSTEMD.value),
    app_commands.Choice(name="Docker", value=Backend.DOCKER.value),
]

SCOPE_CHOICES = [app_commands.Choice(name="全部", value="all"), *BACKEND_CHOICES]


def parse_scope(value: str) -> Backend | None:
    return None if value == "all" else Backend(value)


def service_options(level: Permission):
    """服務相關指令共用的參數說明、自動完成、選項與單一服務權限檢查。"""
    def decorator(func):
        func = require(level, per_service=True)(func)
        func = app_commands.autocomplete(service=service_autocomplete)(func)
        func = app_commands.choices(backend=SCOPE_CHOICES)(func)
        func = app_commands.describe(service="服務名稱", backend="要操作的後端（預設：全部）")(func)

        return func

    return decorator


# 控制類指令的冷卻時間：每位使用者每 5 秒 1 次
control_cooldown = app_commands.checks.cooldown(1, 5.0, key=lambda i: i.user.id)


@app_commands.guild_only()
class Services(commands.GroupCog, group_name="service", group_description="管理服務（systemd / Docker）"):
    def __init__(self, bot: commands.Bot, system_service: SystemService):
        self.bot = bot
        self.system_service = system_service
        super().__init__()

    async def _control(self, interaction: discord.Interaction, service: str, action: Action, backend: str) -> None:
        await interaction.response.defer()

        try:
            results = await self.system_service.control(service, action, parse_scope(backend))

        except (ServiceNotFound, InvalidServiceError) as e:
            await interaction.followup.send(embed=embeds.error(str(e)))
            return

        for result in results:
            audit_log(interaction, action, service, result.success, f"{result.backend}:{result.target} {result.message}")

        await interaction.followup.send(embed=embeds.control_result(service, ACTION_LABELS[action], results))

    # --- 控制指令（操作者以上，可為單一服務授權） ---

    @app_commands.command(name="start", description="啟動服務")
    @service_options(Permission.OPERATOR)
    @control_cooldown
    async def start(self, interaction: discord.Interaction, service: str, backend: str = "all"):
        await self._control(interaction, service, Action.START, backend)

    @app_commands.command(name="stop", description="關閉服務")
    @service_options(Permission.OPERATOR)
    @control_cooldown
    async def stop(self, interaction: discord.Interaction, service: str, backend: str = "all"):
        await self._control(interaction, service, Action.STOP, backend)

    @app_commands.command(name="restart", description="重啟服務")
    @service_options(Permission.OPERATOR)
    @control_cooldown
    async def restart(self, interaction: discord.Interaction, service: str, backend: str = "all"):
        await self._control(interaction, service, Action.RESTART, backend)

    @app_commands.command(name="enable", description="設定開機自動啟動（Docker：restart=unless-stopped）")
    @service_options(Permission.OPERATOR)
    @control_cooldown
    async def enable(self, interaction: discord.Interaction, service: str, backend: str = "all"):
        await self._control(interaction, service, Action.ENABLE, backend)

    @app_commands.command(name="disable", description="取消開機自動啟動（Docker：restart=no）")
    @service_options(Permission.OPERATOR)
    @control_cooldown
    async def disable(self, interaction: discord.Interaction, service: str, backend: str = "all"):
        await self._control(interaction, service, Action.DISABLE, backend)

    # --- 查詢指令（檢視者以上，可為單一服務授權） ---

    @app_commands.command(name="status", description="查詢服務狀態")
    @service_options(Permission.VIEWER)
    async def status(self, interaction: discord.Interaction, service: str, backend: str = "all"):
        await interaction.response.defer()

        try:
            statuses = await self.system_service.status(service, parse_scope(backend))

        except (ServiceNotFound, InvalidServiceError) as e:
            await interaction.followup.send(embed=embeds.error(str(e)))
            return

        await interaction.followup.send(embed=embeds.status(service, statuses))

    @app_commands.command(name="logs", description="查詢服務日誌（僅自己可見）")
    @service_options(Permission.VIEWER)
    @app_commands.describe(lines="每個後端讀取的行數 (1-500，預設 50 行)")
    async def logs(
        self,
        interaction: discord.Interaction,
        service: str,
        backend: str = "all",
        lines: app_commands.Range[int, 1, 500] = 50,
    ):
        # 日誌可能包含敏感資訊，只回覆給呼叫者
        await interaction.response.defer(ephemeral=True)

        try:
            logs = await self.system_service.logs(service, parse_scope(backend), lines)

        except (ServiceNotFound, InvalidServiceError) as e:
            await interaction.followup.send(embed=embeds.error(str(e)), ephemeral=True)
            return

        audit_log(interaction, "logs", service, all(log.success for log in logs), f"backend={backend}")
        log_embeds, truncated = embeds.logs(service, logs, lines)

        # 內容過長時，embed 只顯示最新的部分，並附上完整日誌檔
        files = []

        if truncated:
            content = "\n\n".join(f"===== {log.backend}: {log.target} =====\n{log.message}" for log in logs)
            files.append(discord.File(fp=io.BytesIO(content.encode("utf-8")), filename=f"{service}_logs.txt"))

        await interaction.followup.send(embeds=log_embeds, files=files, ephemeral=True)

    @app_commands.command(name="list", description="列出你可以存取的服務")
    async def service_list(self, interaction: discord.Interaction):
        services = visible_services(interaction)

        if not services:
            await interaction.response.send_message(embed=embeds.error("沒有你可以存取的服務。", title="服務清單"), ephemeral=True)
            return

        await interaction.response.send_message(embed=embeds.service_list(services))

    # --- 管理指令（管理員） ---

    @app_commands.command(name="add", description="新增服務，可同時設定 systemd unit 與 Docker 容器")
    @app_commands.describe(
        name="服務名稱（英數、_、-，最多 32 字）",
        unit="systemd unit 名稱，例如 nginx（預設 .service，也可指定 .socket / .timer）",
        container="Docker 容器名稱",
    )
    @require(Permission.ADMIN)
    async def add(self, interaction: discord.Interaction, name: str, unit: str | None = None, container: str | None = None):
        targets = {backend: target for backend, target in ((Backend.SYSTEMD, unit), (Backend.DOCKER, container)) if target}

        try:
            success = self.system_service.add(name, targets)

        except InvalidServiceError as e:
            await interaction.response.send_message(embed=embeds.error(str(e), title="新增失敗"), ephemeral=True)
            return

        audit_log(interaction, "add", name, success, str(targets))

        if success:
            await interaction.response.send_message(embed=embeds.service_detail("已新增服務", self.system_service.get(name)))
        else:
            await interaction.response.send_message(embed=embeds.error(f"服務 **{name}** 已存在。", title="新增失敗"), ephemeral=True)

    @app_commands.command(name="set", description="為服務設定或更換 systemd unit / Docker 容器")
    @app_commands.describe(service="服務名稱", backend="後端", target="systemd unit 名稱（預設 .service）或 Docker 容器名稱")
    @app_commands.autocomplete(service=service_autocomplete)
    @app_commands.choices(backend=BACKEND_CHOICES)
    @require(Permission.ADMIN)
    async def set_target(self, interaction: discord.Interaction, service: str, backend: str, target: str):
        await self._edit(interaction, "set", service, f"{backend}={target}",
                         lambda: self.system_service.set_target(service, Backend(backend), target))

    @app_commands.command(name="unset", description="移除服務的 systemd unit 或 Docker 容器設定")
    @app_commands.describe(service="服務名稱", backend="要移除的後端")
    @app_commands.autocomplete(service=service_autocomplete)
    @app_commands.choices(backend=BACKEND_CHOICES)
    @require(Permission.ADMIN)
    async def unset_target(self, interaction: discord.Interaction, service: str, backend: str):
        await self._edit(interaction, "unset", service, backend,
                         lambda: self.system_service.unset_target(service, Backend(backend)))

    async def _edit(self, interaction: discord.Interaction, action: str, service: str, detail: str, operation) -> None:
        try:
            updated = operation()

        except (ServiceNotFound, InvalidServiceError) as e:
            await interaction.response.send_message(embed=embeds.error(str(e)), ephemeral=True)
            return

        audit_log(interaction, action, service, True, detail)
        await interaction.response.send_message(embed=embeds.service_detail("服務已更新", updated))

    @app_commands.command(name="remove", description="移除服務（連同其單獨授權）")
    @app_commands.describe(service="服務名稱")
    @app_commands.autocomplete(service=service_autocomplete)
    @require(Permission.ADMIN)
    async def remove(self, interaction: discord.Interaction, service: str):
        success = self.system_service.remove(service)
        audit_log(interaction, "remove", service, success)

        if success:
            self.bot.permissions.repository.remove_service(service)
            await interaction.response.send_message(embed=embeds.success("已移除服務", f"服務 **{service}** 及其單獨授權已移除。"))
        else:
            await interaction.response.send_message(embed=embeds.error(f"找不到服務 **{service}**"), ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Services(bot, bot.system_service))
