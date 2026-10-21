from pathlib import Path

import pytest

from system_controller.core.config import ConfigError, load_settings


def test_requires_token():
    with pytest.raises(ConfigError):
        load_settings({})


def test_accepts_legacy_token_key():
    assert load_settings({"bot_token": "abc"}).token == "abc"


def test_parses_ids_and_options():
    settings = load_settings({
        "BOT_TOKEN": "abc",
        "ADMIN_USER_IDS": "1, 2,,3",
        "OPERATOR_ROLE_IDS": "44",
        "SERVICES_FILE": "/var/lib/sc/services.json",
        "DOCKER_USE_SUDO": "false",
    })

    assert settings.admin_user_ids == {1, 2, 3}
    assert settings.operator_role_ids == {44}
    assert settings.viewer_user_ids == frozenset()
    assert settings.services_file == Path("/var/lib/sc/services.json")
    assert settings.docker_use_sudo is False


@pytest.mark.parametrize("env", [
    {"ADMIN_USER_IDS": "12a"},
    {"VIEWER_ROLE_IDS": "-1"},
    {"DOCKER_USE_SUDO": "maybe"},
])
def test_rejects_invalid_values(env):
    with pytest.raises(ConfigError):
        load_settings({"BOT_TOKEN": "abc", **env})
