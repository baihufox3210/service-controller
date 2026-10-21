from collections.abc import Sequence
from enum import Enum

import discord

from system_controller.core.interactions import code_block
from system_controller.models.service import Backend, Service
from system_controller.services.system_service import BackendResult, BackendStatus


class Color:
    SUCCESS = discord.Color.from_rgb(46, 204, 113)
    FAILURE = discord.Color.from_rgb(231, 76, 60)
    WARNING = discord.Color.from_rgb(241, 196, 15)
    INFO = discord.Color.from_rgb(88, 101, 242)


class Health(Enum):
    RUNNING = ("運作中", Color.SUCCESS)
    TRANSITION = ("轉換中", Color.WARNING)
    STOPPED = ("已停止", Color.WARNING)
    FAILED = ("異常", Color.FAILURE)

    @property
    def label(self) -> str:
        return self.value[0]

    @property
    def color(self) -> discord.Color:
        return self.value[1]


# systemd 與 docker 的狀態分類
RUNNING_STATES = {"active", "running"}
TRANSITION_STATES = {"activating", "deactivating", "reloading", "restarting", "refreshing", "removing"}
FAILED_STATES = {"failed", "dead", "not-found", "unknown"}

# Discord 限制：單一 embed 描述 4096 字、單則訊息所有 embed 合計 6000 字
EMBED_DESCRIPTION_LIMIT = 4096
MESSAGE_EMBED_LIMIT = 6000


def health_of(state: str) -> Health:
    if state in RUNNING_STATES: return Health.RUNNING
    if state in TRANSITION_STATES: return Health.TRANSITION
    if state in FAILED_STATES: return Health.FAILED

    return Health.STOPPED


def backend_label(backend: Backend, target: str) -> str:
    return f"{backend} · {target}"


def success(title: str, description: str | None = None) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=Color.SUCCESS)


def error(message: str, title: str = "操作失敗") -> discord.Embed:
    return discord.Embed(title=title, description=message, color=Color.FAILURE)


def service_detail(title: str, service: Service) -> discord.Embed:
    embed = success(title, f"服務 **{service.name}**")

    for backend, target in service.targets.items():
        embed.add_field(name=str(backend), value=f"`{target}`")

    return embed


def control_result(service: str, action_label: str, results: Sequence[BackendResult]) -> discord.Embed:
    succeeded = sum(result.success for result in results)

    if succeeded == len(results):
        title, color = f"{action_label}成功", Color.SUCCESS
    elif succeeded:
        title, color = f"部分{action_label}失敗", Color.WARNING
    else:
        title, color = f"{action_label}失敗", Color.FAILURE

    embed = discord.Embed(title=title, description=f"服務 **{service}**", color=color)

    for result in results:
        value = "成功" if result.success else f"失敗\n{code_block(result.message, limit=900)}"
        embed.add_field(name=backend_label(result.backend, result.target), value=value, inline=False)

    return embed


def status(service: str, statuses: Sequence[BackendStatus]) -> discord.Embed:
    healths = [health_of(s.active) for s in statuses]

    # 顏色取最嚴重的狀態
    if Health.FAILED in healths:
        color = Color.FAILURE
    elif all(health is Health.RUNNING for health in healths):
        color = Color.SUCCESS
    else:
        color = Color.WARNING

    embed = discord.Embed(title=f"{service} 狀態", color=color)

    for s, health in zip(statuses, healths, strict=True):
        embed.add_field(
            name=backend_label(s.backend, s.target),
            value=f"執行狀態：**{health.label}**（`{s.active}`）\n開機啟動：`{s.enabled}`",
            inline=False,
        )

    return embed


def _tail(text: str, limit: int) -> tuple[str, bool]:
    """保留最後 limit 個字元（從完整的一行開始），回傳 (內容, 是否被截斷)。"""
    if len(text) <= limit: return text, False

    text = text[-limit:]
    newline = text.find("\n")

    return (text[newline + 1:] if 0 <= newline < len(text) - 1 else text), True


def logs(service: str, logs: Sequence[BackendResult], lines: int) -> tuple[list[discord.Embed], bool]:
    """每個後端一個 embed；內容過長時保留最新的部分，並回傳是否需要附上完整檔案。"""
    # 預留標題與程式碼區塊標記的空間
    budget = min(EMBED_DESCRIPTION_LIMIT, MESSAGE_EMBED_LIMIT // max(len(logs), 1)) - 200
    embed_list, truncated = [], False

    for log in logs:
        content, cut = _tail(log.message, budget)
        truncated |= cut

        embed = discord.Embed(
            title=f"{service} 日誌",
            description=code_block(content, limit=budget),
            color=Color.INFO if log.success else Color.FAILURE,
        )
        embed.set_author(name=backend_label(log.backend, log.target))
        embed.set_footer(text=f"最近 {lines} 行" + ("，已截斷，完整內容見附件" if cut else ""))
        embed_list.append(embed)

    return embed_list, truncated


def service_list(services: Sequence[Service]) -> discord.Embed:
    lines = [
        f"**{service.name}**\n" + "\n".join(f"　{backend}：`{target}`" for backend, target in service.targets.items())
        for service in services
    ]

    embed = discord.Embed(title="服務清單", description="\n\n".join(lines)[:EMBED_DESCRIPTION_LIMIT], color=Color.INFO)
    embed.set_footer(text=f"共 {len(services)} 個服務")

    return embed
