from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from system_controller.adapters.base import CommandResult

GUILD_ID = 100


def ok(stdout: str = "") -> CommandResult:
    return CommandResult(code=0, stdout=stdout, stderr="")


def fail(stderr: str = "error") -> CommandResult:
    return CommandResult(code=1, stdout="", stderr=stderr)


def make_interaction(client, user_id: int, role_ids=(), guild_id=GUILD_ID, **namespace):
    """模擬 discord.Interaction 中本專案會用到的欄位。"""
    return SimpleNamespace(
        client=client,
        user=SimpleNamespace(id=user_id, roles=[SimpleNamespace(id=r) for r in role_ids]),
        guild_id=guild_id,
        channel_id=1,
        namespace=SimpleNamespace(**namespace),
        response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
        followup=SimpleNamespace(send=AsyncMock()),
    )


def sent_text(mock) -> str:
    """把最後一次送出的訊息（文字與 embed 的標題、描述、欄位）合併成文字，方便斷言。"""
    call = mock.await_args
    parts = list(call.args)
    embed_list = call.kwargs.get("embeds") or ([call.kwargs["embed"]] if call.kwargs.get("embed") else [])

    for embed in embed_list:
        parts += [embed.title or "", embed.description or "", embed.author.name or "", embed.footer.text or ""]
        parts += [f"{field.name}\n{field.value}" for field in embed.fields]

    return "\n".join(str(part) for part in parts)


@pytest.fixture
def tmp_db(tmp_path):
    return SimpleNamespace(services=tmp_path / "service.json", permissions=tmp_path / "permissions.json")
