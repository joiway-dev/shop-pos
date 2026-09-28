from app import templating
from tests.conftest import OWNER_PIN, login


def test_banner_when_program_files_are_newer(client, owner, monkeypatch):
    login(client, owner, OWNER_PIN)
    monkeypatch.setitem(templating._update_check, "at", 0.0)
    assert "โปรแกรมถูกอัปเดตแล้ว" not in client.get("/").text

    monkeypatch.setattr(templating, "_STARTED_CODE_MTIME", 0.0)  # pretend the server is old
    monkeypatch.setitem(templating._update_check, "at", 0.0)
    assert "โปรแกรมถูกอัปเดตแล้ว" in client.get("/").text
