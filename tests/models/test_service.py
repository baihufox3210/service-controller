import pytest

from system_controller.models.service import Backend, InvalidServiceError, Service


def test_targets_are_normalized_and_ordered():
    service = Service("web", {"docker": "web", "systemd": "web.service"})

    assert list(service.targets) == [Backend.SYSTEMD, Backend.DOCKER]


def test_select_scope():
    service = Service("web", {Backend.SYSTEMD: "web.service", Backend.DOCKER: "web"})

    assert service.select() == [(Backend.SYSTEMD, "web.service"), (Backend.DOCKER, "web")]
    assert service.select(Backend.DOCKER) == [(Backend.DOCKER, "web")]


def test_select_unconfigured_backend():
    with pytest.raises(InvalidServiceError):
        Service("web", {Backend.SYSTEMD: "web.service"}).select(Backend.DOCKER)


@pytest.mark.parametrize(
    ("name", "targets"),
    [
        ("bad name", {Backend.SYSTEMD: "ok.service"}),
        ("x" * 33, {Backend.SYSTEMD: "ok.service"}),
        ("ok", {}),
        ("ok", {Backend.SYSTEMD: "--help"}),
        ("ok", {Backend.SYSTEMD: "multi-user.target"}),
        ("ok", {Backend.SYSTEMD: "home.mount"}),
        ("ok", {Backend.SYSTEMD: "-H"}),
        ("ok", {Backend.SYSTEMD: "../x.service"}),
        ("ok", {Backend.DOCKER: "-rm"}),
        ("ok", {Backend.DOCKER: "a/b"}),
        ("ok", {"k8s": "pod"}),
    ],
)
def test_rejects_invalid(name, targets):
    with pytest.raises(InvalidServiceError):
        Service(name, targets)


@pytest.mark.parametrize(
    ("unit", "expected"),
    [
        ("nginx", "nginx.service"),
        ("nginx.service", "nginx.service"),
        ("backup.timer", "backup.timer"),
        ("docker.socket", "docker.socket"),
        ("getty@tty1", "getty@tty1.service"),
    ],
)
def test_systemd_suffix_defaults_to_service(unit, expected):
    assert Service("ok", {Backend.SYSTEMD: unit}).targets[Backend.SYSTEMD] == expected


def test_invalid_unit_error_shows_original_input():
    with pytest.raises(InvalidServiceError, match="'a b'"):
        Service("ok", {Backend.SYSTEMD: "a b"})
