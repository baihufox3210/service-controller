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
        ("ok", {Backend.SYSTEMD: "nginx"}),
        ("ok", {Backend.SYSTEMD: "../x.service"}),
        ("ok", {Backend.DOCKER: "-rm"}),
        ("ok", {Backend.DOCKER: "a/b"}),
        ("ok", {"k8s": "pod"}),
    ],
)
def test_rejects_invalid(name, targets):
    with pytest.raises(InvalidServiceError):
        Service(name, targets)
