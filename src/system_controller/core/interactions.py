import logging

import discord
from discord import app_commands

from system_controller.core.permissions import get_policy
from system_controller.models.permission import Permission

audit = logging.getLogger("system_controller.audit")

MAX_MESSAGE_LENGTH = 1900


def code_block(text: str, limit: int = MAX_MESSAGE_LENGTH) -> str:
    # 避免輸出內容提前關閉程式碼區塊
    text = (text or "(無輸出)").replace("```", "`​``")

    return f"```\n{text[:limit]}\n```"


def audit_log(interaction: discord.Interaction, action: str, target: str, success: bool, detail: str = "") -> None:
    audit.info(
        "user=%s(%s) guild=%s channel=%s action=%s target=%s success=%s %s",
        interaction.user, interaction.user.id, interaction.guild_id, interaction.channel_id,
        action, target, success, detail,
    )


def visible_services(interaction: discord.Interaction) -> list:
    """回傳使用者有檢視權限（全域或單一服務）的服務。"""
    try:
        policy = get_policy(interaction)

    except app_commands.CheckFailure:
        return []

    return [
        service for service in interaction.client.system_service.get_all()
        if policy.level_for_interaction(interaction, service.name) >= Permission.VIEWER
    ]


async def service_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    # 只顯示使用者看得到的服務，避免洩漏服務清單
    current = current.lower()

    return [
        app_commands.Choice(name=f"{s.name} ({', '.join(f'{b}: {t}' for b, t in s.targets.items())})"[:100], value=s.name)
        for s in visible_services(interaction)
        if current in s.name.lower() or any(current in t.lower() for t in s.targets.values())
    ][:25]
