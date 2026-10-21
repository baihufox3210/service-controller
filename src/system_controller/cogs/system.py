import io
import logging

import discord
from discord import app_commands
from discord.ext import commands

from system_controller.core.permissions import Permission, get_policy, require
from system_controller.models.service import Backend, InvalidServiceError
from system_controller.services.system_service import ServiceOperationResult, SystemService

audit = logging.getLogger("system_controller.audit")

MAX_INLINE_LENGTH = 1800

# 控制類指令的冷卻時間：每位使用者每 5 秒 1 次
control_cooldown = app_commands.checks.cooldown(1, 5.0, key=lambda i: i.user.id)


def code_block(text: str) -> str:
    # 避免輸出內容提前關閉程式碼區塊
    text = (text or "(無輸出)").replace("```", "`​``")

    return f"```\n{text[:MAX_INLINE_LENGTH]}\n```"


def audit_log(interaction: discord.Interaction, action: str, service: str, success: bool, detail: str = "") -> None:
    audit.info(
        "user=%s(%s) guild=%s channel=%s action=%s service=%s success=%s %s",
        interaction.user, interaction.user.id, interaction.guild_id, interaction.channel_id,
        action, service, success, detail,
    )


@app_commands.guild_only()
class Services(commands.GroupCog, group_name="service", group_description="管理系統服務（systemd / Docker）"):
    def __init__(self, bot: commands.Bot, system_service: SystemService):
        self.bot = bot
        self.system_service = system_service
        super().__init__()

    async def service_autocomplete(self, interaction: discord.Interaction, current: str):
        # 沒有檢視權限的使用者不顯示任何服務，避免洩漏服務清單
        try:
            if get_policy(interaction).level_for_interaction(interaction) < Permission.VIEWER:
                return []

        except app_commands.CheckFailure:
            return []

        current = current.lower()

        return [
            app_commands.Choice(name=f"{s.name} ({s.backend}: {s.target})"[:100], value=s.name)
            for s in self.system_service.get_all()
            if current in s.name.lower() or current in s.target.lower()
        ][:25]

    async def _control(self, interaction: discord.Interaction, action: str, service: str, label: str) -> None:
        await interaction.response.defer()
        result: ServiceOperationResult = await getattr(self.system_service, action)(service)
        audit_log(interaction, action, service, result.success, "" if result.success else result.message)

        if result.success:
            await interaction.followup.send(f"`{service}` {label}成功！")
        else:
            await interaction.followup.send(f"`{service}` {label}失敗：\n{code_block(result.message)}")

    # --- 服務狀態控制指令（操作者以上） ---

    @app_commands.command(name="start", description="啟動服務")
    @app_commands.describe(service="要啟動的服務名稱")
    @app_commands.autocomplete(service=service_autocomplete)
    @require(Permission.OPERATOR)
    @control_cooldown
    async def start(self, interaction: discord.Interaction, service: str):
        await self._control(interaction, "start", service, "啟動")

    @app_commands.command(name="stop", description="關閉服務")
    @app_commands.describe(service="要關閉的服務名稱")
    @app_commands.autocomplete(service=service_autocomplete)
    @require(Permission.OPERATOR)
    @control_cooldown
    async def stop(self, interaction: discord.Interaction, service: str):
        await self._control(interaction, "stop", service, "關閉")

    @app_commands.command(name="restart", description="重啟服務")
    @app_commands.describe(service="要重啟的服務名稱")
    @app_commands.autocomplete(service=service_autocomplete)
    @require(Permission.OPERATOR)
    @control_cooldown
    async def restart(self, interaction: discord.Interaction, service: str):
        await self._control(interaction, "restart", service, "重啟")

    @app_commands.command(name="enable", description="設定服務開機自動啟動（Docker：restart=unless-stopped）")
    @app_commands.describe(service="要設定的服務名稱")
    @app_commands.autocomplete(service=service_autocomplete)
    @require(Permission.OPERATOR)
    @control_cooldown
    async def enable(self, interaction: discord.Interaction, service: str):
        await self._control(interaction, "enable", service, "設定開機自動啟動")

    @app_commands.command(name="disable", description="取消服務開機自動啟動（Docker：restart=no）")
    @app_commands.describe(service="要取消的服務名稱")
    @app_commands.autocomplete(service=service_autocomplete)
    @require(Permission.OPERATOR)
    @control_cooldown
    async def disable(self, interaction: discord.Interaction, service: str):
        await self._control(interaction, "disable", service, "取消開機自動啟動")

    # --- 查詢與資訊指令（檢視者以上） ---

    @app_commands.command(name="status", description="查詢服務狀態")
    @app_commands.describe(service="要查詢的服務名稱")
    @app_commands.autocomplete(service=service_autocomplete)
    @require(Permission.VIEWER)
    async def status(self, interaction: discord.Interaction, service: str):
        await interaction.response.defer()
        status_info = await self.system_service.status(service)

        if status_info is None:
            await interaction.followup.send(f"找不到服務 `{service}`。")
            return

        await interaction.followup.send(
            f"**服務 `{service}` 狀態：**\n"
            f"• Backend: `{status_info.service.backend}`\n"
            f"• Target: `{status_info.service.target}`\n"
            f"• Active State: `{status_info.active}`\n"
            f"• Enabled State: `{status_info.enabled}`"
        )

    @app_commands.command(name="logs", description="查詢服務日誌（僅自己可見）")
    @app_commands.describe(service="要調閱的服務名稱", lines="讀取行數 (1-500，預設 50 行)")
    @app_commands.autocomplete(service=service_autocomplete)
    @require(Permission.VIEWER)
    async def logs(self, interaction: discord.Interaction, service: str, lines: app_commands.Range[int, 1, 500] = 50):
        # 日誌可能包含敏感資訊，只回覆給呼叫者
        await interaction.response.defer(ephemeral=True)
        log_info = await self.system_service.logs(service, lines=lines)
        audit_log(interaction, "logs", service, log_info is not None and log_info.success)

        if log_info is None:
            await interaction.followup.send(f"找不到服務 `{service}`。", ephemeral=True)
            return

        content = log_info.content

        if len(content) <= MAX_INLINE_LENGTH:
            await interaction.followup.send(f"服務 `{service}` 日誌 ({lines} 行)：\n{code_block(content)}", ephemeral=True)
        else:
            log_file = discord.File(
                fp=io.BytesIO(content.encode("utf-8")), filename=f"{service}_logs.txt",
            )
            await interaction.followup.send(
                f"服務 `{service}` 的日誌過長，已改以文字檔傳送：", file=log_file, ephemeral=True,
            )

    @app_commands.command(name="list", description="列出所有服務")
    @require(Permission.VIEWER)
    async def service_list(self, interaction: discord.Interaction):
        await interaction.response.defer()
        services = self.system_service.get_all()

        if not services:
            await interaction.followup.send("目前尚未註冊任何服務。請使用 `/service add` 新增。")
            return

        formatted_list = [f"- {s.name}: `{s.backend}` / `{s.target}`" for s in services]
        await interaction.followup.send("**可管理服務清單：**\n" + "\n".join(formatted_list))

    @app_commands.command(name="whoami", description="查看自己的服務控制權限")
    async def whoami(self, interaction: discord.Interaction):
        level = get_policy(interaction).level_for_interaction(interaction)
        await interaction.response.send_message(f"你目前的權限：**{level.label}**", ephemeral=True)

    # --- 管理維護指令（管理員） ---

    @app_commands.command(name="add", description="新增服務")
    @app_commands.describe(
        name="服務名稱（英數、_、-，最多 32 字）",
        backend="服務類型",
        target="systemd unit 名稱（例如 nginx.service）或 Docker 容器名稱",
    )
    @app_commands.choices(backend=[
        app_commands.Choice(name="systemd", value=Backend.SYSTEMD.value),
        app_commands.Choice(name="Docker 容器", value=Backend.DOCKER.value),
    ])
    @require(Permission.ADMIN)
    async def add(self, interaction: discord.Interaction, name: str, backend: app_commands.Choice[str], target: str):
        await interaction.response.defer()

        try:
            success = self.system_service.add(name, target, backend.value)

        except InvalidServiceError as e:
            audit_log(interaction, "add", name, False, str(e))
            await interaction.followup.send(f"新增失敗：\n{code_block(str(e))}")
            return

        audit_log(interaction, "add", name, success, f"backend={backend.value} target={target}")

        if success:
            await interaction.followup.send(f"成功新增服務：`{name}` (`{backend.value}` / `{target}`)")
        else:
            await interaction.followup.send(f"新增失敗：服務名稱 `{name}` 已存在。")

    @app_commands.command(name="remove", description="移除已註冊的服務")
    @app_commands.describe(service="要移除的服務名稱")
    @app_commands.autocomplete(service=service_autocomplete)
    @require(Permission.ADMIN)
    async def remove(self, interaction: discord.Interaction, service: str):
        await interaction.response.defer()
        success = self.system_service.remove(service)
        audit_log(interaction, "remove", service, success)

        if success:
            await interaction.followup.send(f"已成功移除服務：`{service}`")
        else:
            await interaction.followup.send(f"移除失敗：找不到服務 `{service}`。")


async def setup(bot: commands.Bot):
    await bot.add_cog(Services(bot, bot.system_service))
