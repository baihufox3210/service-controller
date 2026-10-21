import discord
from discord.ext import commands

from system_controller.cogs.system import Services, code_block
from system_controller.repositories.repository import ServiceRepository
from system_controller.services.system_service import SystemService


def test_cog_registers_guarded_commands(tmp_path):
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    cog = Services(bot, SystemService(ServiceRepository(tmp_path / "service.json")))

    group = cog.app_command
    names = {command.name for command in group.commands}

    assert group.guild_only is True
    assert names == {"start", "stop", "restart", "enable", "disable", "status", "logs", "list", "whoami", "add", "remove"}

    # whoami 以外的所有指令都必須帶有權限檢查
    for command in group.commands:
        if command.name != "whoami":
            assert command.checks, f"{command.name} has no permission check"


def test_code_block_cannot_be_escaped():
    assert code_block("a```b") == "```\na`​``b\n```"
