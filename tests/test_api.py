"""웹 API. 시스템 DB 없이 도는 부분(표준 조회·검증, 정보)만 확인한다."""
import pytest
from fastapi.testclient import TestClient

from nlxpg.api import app as app_module
from nlxpg.auth import User

ADMIN = User(1, "admin", "관리자", None, "admin", True, False)


@pytest.fixture
def client(monkeypatch):
    async def no_db(dsn):
        raise ConnectionError("테스트에서는 DB를 쓰지 않는다")

    monkeypatch.setattr(app_module.RunStore, "connect", staticmethod(no_db))
    app_module.app.dependency_overrides[app_module.session_user] = lambda: ADMIN
    with TestClient(app_module.app) as c:
        yield c
    app_module.app.dependency_overrides.clear()


def test_login_required_without_override(monkeypatch):
    async def no_db(dsn):
        raise ConnectionError("x")

    monkeypatch.setattr(app_module.RunStore, "connect", staticmethod(no_db))
    with TestClient(app_module.app) as c:
        # 로그인 정보 없이 API를 부르면 막힌다 (DB가 없으면 503, 있으면 401)
        assert c.get("/api/runs").status_code in (401, 503)
        assert c.get("/").status_code == 200  # 화면(로그인 화면 포함)은 열린다


def test_must_change_password_blocks_other_api(monkeypatch):
    async def no_db(dsn):
        raise ConnectionError("x")

    monkeypatch.setattr(app_module.RunStore, "connect", staticmethod(no_db))
    pending = User(2, "kim", "김", None, "user", True, True)
    app_module.app.dependency_overrides[app_module.session_user] = lambda: pending
    with TestClient(app_module.app) as c:
        assert c.get("/api/auth/me").json()["must_change_password"] is True
        assert c.get("/api/standards/search", params={"q": "거래처"}).status_code == 403
    app_module.app.dependency_overrides.clear()


def test_index_and_info(client):
    assert "nlxpg" in client.get("/").text
    info = client.get("/api/info").json()
    assert info["db_ok"] is False and "ConnectionError" in info["db_error"]
    assert info["standards_version"]


def test_runs_need_db(client):
    assert client.get("/api/runs").status_code == 503


def test_standards_lookup_and_search(client):
    rows = client.get("/api/standards/lookup", params={"names": "거래처명\n사업자번호"}).json()
    assert [r["physical_name"] for r in rows] == ["cnpt_nm", "brno"]
    res = client.get("/api/standards/search", params={"q": "CNPT_NM", "kind": "term"}).json()
    assert res["items"][0]["name"] == "거래처명"


def test_standards_check_requires_input(client):
    r = client.post("/api/standards/check", json={"source": "db"})
    assert r.status_code == 400


def test_standards_domains(client):
    d = client.get("/api/standards/domains").json()
    assert d["total"] == sum(len(g["domains"]) for g in d["groups"])
    assert [g["group"] for g in d["groups"]][:2] == ["금액", "날짜/시간"]  # 매뉴얼 표 Ⅳ-29 순서
    ymd = next(x for g in d["groups"] for x in g["domains"] if x["name"] == "연월일C8")
    assert (ymd["original_type"], ymd["pg_type"], ymd["adapted"]) == ("char(8)", "date", True)
    assert ymd["term_count"] > 0 and "일자" in d["format_words"]["연월일"]
    terms = client.get("/api/standards/domains/연월일C8/terms", params={"limit": 5}).json()
    assert terms["total"] == ymd["term_count"] and len(terms["items"]) == 5
    assert client.get("/api/standards/domains/없는도메인/terms").status_code == 404
