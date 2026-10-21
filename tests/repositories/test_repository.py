import json
import os
import stat

from system_controller.models.service import Backend, Service
from system_controller.repositories.repository import ServiceRepository


def test_roundtrip(tmp_path):
    path = tmp_path / "db" / "service.json"
    repository = ServiceRepository(path)

    assert repository.add(Service("nginx", "nginx.service")) is True
    assert repository.add(Service("web", "my-web", Backend.DOCKER)) is True
    assert repository.add(Service("web", "other.service")) is False

    reloaded = ServiceRepository(path)

    assert reloaded.get("web") == Service("web", "my-web", Backend.DOCKER)
    assert reloaded.get("nginx").backend is Backend.SYSTEMD
    assert json.loads(path.read_text())["web"] == {"backend": "docker", "target": "my-web"}
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_loads_legacy_format_and_skips_invalid(tmp_path):
    path = tmp_path / "service.json"
    path.write_text(json.dumps({
        "nginx": "nginx.service",
        "evil": "--help",
        "bad name": "x.service",
        "web": {"backend": "docker", "target": "my-web"},
        "unknown": {"backend": "k8s", "target": "pod"},
    }))

    repository = ServiceRepository(path)

    assert sorted(s.name for s in repository.get_all()) == ["nginx", "web"]


def test_remove(tmp_path):
    path = tmp_path / "service.json"
    repository = ServiceRepository(path)
    repository.add(Service("nginx", "nginx.service"))

    assert repository.remove("nginx") is True
    assert repository.remove("nginx") is False
    assert ServiceRepository(path).get_all() == []
    assert [p.name for p in tmp_path.iterdir()] == ["service.json"]  # 沒有殘留暫存檔
