from pathlib import Path

import pytest

from system_controller.core.config import ConfigError, load_settings


def test_requires_token():
    with pytest.raises(ConfigError):
        load_settings({})


def test_parses_options():
    settings = load_settings({
        "BOT_TOKEN": "abc",
        "OWNER_USER_IDS": "1, 2,,3",
        "ALLOWED_GUILD_IDS": "44",
        "PERMISSIONS_FILE": "/var/lib/sc/permissions.json",
        "DOCKER_USE_SUDO": "false",
    })

    assert settings.owner_user_ids == {1, 2, 3}
    assert settings.allowed_guild_ids == {44}
    assert settings.permissions_file == Path("/var/lib/sc/permissions.json")
    assert settings.docker_use_sudo is False


@pytest.mark.parametrize("env", [{"OWNER_USER_IDS": "12a"}, {"ALLOWED_GUILD_IDS": "-1"}, {"DOCKER_USE_SUDO": "maybe"}])
def test_rejects_invalid_values(env):
    with pytest.raises(ConfigError):
        load_settings({"BOT_TOKEN": "abc", **env})
