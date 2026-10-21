import json
import os
import stat

from system_controller.models.permission import Grant, Permission, SubjectType
from system_controller.models.service import Backend, Service
from system_controller.repositories.permission_repository import PermissionRepository
from system_controller.repositories.service_repository import ServiceRepository


def test_service_roundtrip(tmp_db):
    repository = ServiceRepository(tmp_db.services)
    web = Service("web", {Backend.SYSTEMD: "web.service", Backend.DOCKER: "web"})

    assert repository.add(web) is True
    assert repository.add(Service("web", {Backend.DOCKER: "other"})) is False

    repository.replace(Service("web", {Backend.DOCKER: "web2"}))

    assert ServiceRepository(tmp_db.services).get("web").targets == {Backend.DOCKER: "web2"}
    assert json.loads(tmp_db.services.read_text()) == {"web": {"docker": "web2"}}
    assert stat.S_IMODE(os.stat(tmp_db.services).st_mode) == 0o600

    assert repository.remove("web") is True
    assert repository.remove("web") is False
    assert [p.name for p in tmp_db.services.parent.iterdir()] == ["service.json"]  # 沒有殘留暫存檔


def test_service_skips_invalid_entries(tmp_db):
    tmp_db.services.write_text(json.dumps({
        "nginx": {"systemd": "nginx.service"},
        "string": "nginx.service",
        "web": {"systemd": "web.service", "docker": "web"},
        "evil": "--help",
        "empty": {},
        "unknown": {"k8s": "pod"},
        "bad": 123,
    }))

    repository = ServiceRepository(tmp_db.services)

    assert sorted(s.name for s in repository.get_all()) == ["nginx", "web"]
    assert repository.get("nginx").targets == {Backend.SYSTEMD: "nginx.service"}


def test_permission_roundtrip_and_matching(tmp_db):
    repository = PermissionRepository(tmp_db.permissions)
    repository.set(Grant(SubjectType.USER, 1, Permission.ADMIN))
    repository.set(Grant(SubjectType.ROLE, 10, Permission.OPERATOR, "web"))
    repository.set(Grant(SubjectType.ROLE, 10, Permission.VIEWER, "web"))  # 覆寫

    reloaded = PermissionRepository(tmp_db.permissions)

    assert len(reloaded.find()) == 2
    assert reloaded.get(SubjectType.ROLE, 10, "web").level is Permission.VIEWER
    assert [g.level for g in reloaded.matching(2, [10], "web")] == [Permission.VIEWER]
    assert reloaded.matching(2, [10], "db") == []
    assert reloaded.matching(2, [10]) == []  # 單一服務授權不會變成全域權限


def test_permission_remove_and_remove_service(tmp_db):
    repository = PermissionRepository(tmp_db.permissions)
    repository.set(Grant(SubjectType.USER, 1, Permission.VIEWER))
    repository.set(Grant(SubjectType.USER, 1, Permission.OPERATOR, "web"))

    assert repository.remove(SubjectType.USER, 1, "db") is None
    assert repository.remove(SubjectType.USER, 1).level is Permission.VIEWER

    repository.remove_service("web")

    assert PermissionRepository(tmp_db.permissions).find() == []


def test_permission_skips_invalid_entries(tmp_db):
    tmp_db.permissions.write_text(json.dumps({"grants": [
        {"type": "user", "id": 1, "level": "admin", "service": None},
        {"type": "user", "id": 2, "level": "admin", "service": "web"},  # 單一服務不可為管理員
        {"type": "user", "id": 3, "level": "owner"},                    # 擁有者不可授予
        {"type": "group", "id": 4, "level": "viewer"},
        {"id": 5},
    ]}))

    assert [g.subject_id for g in PermissionRepository(tmp_db.permissions).find()] == [1]
