"""앱(로컬 웹 프로그램) 테스트.

브라우저 없이 Flask 테스트 클라이언트로 돈다. 화면 조작은 브라우저로 따로
확인했고, 여기서는 사용자가 실제로 밟는 경로가 끊기지 않는지를 지킨다.
"""

from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mogosa.app import create_app

sys.path.insert(0, str(Path(__file__).resolve().parent))
from make_fixture_pdf import build as build_fixture

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "mogosa" / "examples" / "sample_exam.json"
STANDARDS = ROOT / "mogosa" / "data" / "standards_example.json"


@pytest.fixture()
def client():
    app = create_app()
    app.config.update(TESTING=True)
    with app.test_client() as test_client:
        yield test_client


def upload(path: Path, name: str | None = None):
    return (io.BytesIO(path.read_bytes()), name or path.name)


@pytest.fixture(scope="module")
def fixture_pdf(tmp_path_factory) -> Path:
    return build_fixture(tmp_path_factory.mktemp("app_pdf") / "기출.pdf")


# ---------- 기본 ----------


def test_page_loads(client):
    response = client.get("/")
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "모의고사 도우미" in body
    assert "한글 파일 만들기" in body


def test_page_is_offline_only(client):
    """바깥에서 무엇도 받아오면 안 된다. 시험 문항이 새면 큰일이다."""
    body = client.get("/").get_data(as_text=True)
    for marker in ("http://", "https://", "cdn.", "//fonts."):
        assert marker not in body.replace("http://127.0.0.1", ""), f"외부 참조 발견: {marker}"


def test_health(client):
    assert client.get("/health").get_json()["ok"] is True


# ---------- 변환 ----------


def test_convert_json_without_standards(client):
    response = client.post("/convert", data={
        "file": upload(SAMPLE),
        "subject": "생활과 윤리",
        "columns": "2",
    }, content_type="multipart/form-data")

    assert response.status_code == 200
    data = response.get_json()
    assert data["questions"] == 4
    assert data["has_standards"] is False
    assert "student" in data["downloads"]
    assert len(data["rows"]) == 4


def test_convert_json_with_standards(client):
    response = client.post("/convert", data={
        "file": upload(SAMPLE),
        "standards": upload(STANDARDS),
        "answers": "on",
        "spec_table": "on",
    }, content_type="multipart/form-data")

    data = response.get_json()
    assert data["has_standards"] is True
    assert data["standard_count"] == 6
    assert data["rows"][0]["standard"] == "12생윤04-02"
    assert data["unmapped"] == [4], "판정 못 한 문항은 비워 두고 알려야 한다"
    assert "teacher" in data["downloads"]


def test_convert_pdf(client, fixture_pdf: Path):
    response = client.post("/convert", data={
        "file": upload(fixture_pdf),
        "standards": upload(STANDARDS),
        "subject": "생활과 윤리",
    }, content_type="multipart/form-data")

    data = response.get_json()
    assert data["questions"] == 6
    assert any(w.startswith("읽은 결과") for w in data["warnings"])


def test_candidates_are_offered_for_review(client):
    """미판정 문항에도 판단 근거를 보여 줘야 사람이 고를 수 있다."""
    data = client.post("/convert", data={
        "file": upload(SAMPLE),
        "standards": upload(STANDARDS),
    }, content_type="multipart/form-data").get_json()

    first = data["rows"][0]
    assert first["candidates"], "후보 목록이 있어야 한다"
    assert first["candidates"][0]["code"] == "12생윤04-02"
    assert first["candidates"][0]["matched"], "근거 낱말이 있어야 한다"
    assert first["candidates"][0]["summary"], "성취기준 내용 요약이 있어야 한다"


# ---------- 다운로드 ----------


def test_downloads_are_valid_hwpx(client):
    data = client.post("/convert", data={
        "file": upload(SAMPLE),
        "standards": upload(STANDARDS),
        "answers": "on",
        "spec_table": "on",
    }, content_type="multipart/form-data").get_json()

    for kind in ("student", "teacher"):
        response = client.get(data["downloads"][kind])
        assert response.status_code == 200
        archive = zipfile.ZipFile(io.BytesIO(response.data))
        assert "Contents/section0.xml" in archive.namelist()
        assert archive.read("mimetype").startswith(b"application/hwp+zip")


def test_json_download_round_trips(client):
    data = client.post("/convert", data={
        "file": upload(SAMPLE),
        "standards": upload(STANDARDS),
    }, content_type="multipart/form-data").get_json()

    payload = json.loads(client.get(data["downloads"]["json"]).data)
    assert payload["questions"][0]["standard"] == "12생윤04-02"


# ---------- 성취기준 직접 수정 ----------


def test_edit_standard_and_rerender(client):
    data = client.post("/convert", data={
        "file": upload(SAMPLE),
        "standards": upload(STANDARDS),
        "answers": "on",
        "spec_table": "on",
    }, content_type="multipart/form-data").get_json()
    token = data["token"]
    assert data["rows"][3]["standard"] == ""

    updated = client.post("/update", json={
        "token": token,
        "standards": {"4": "12생윤05-02"},
        "answers": True,
        "spec_table": True,
    }).get_json()

    assert updated["rows"][3]["standard"] == "12생윤05-02"
    assert updated["rows"][3]["confidence"] == 1.0, "사람이 정한 값은 확신 1.0"
    assert updated["unmapped"] == []

    from hwpx.document import HwpxDocument

    body = client.get(updated["downloads"]["teacher"]).data
    path = Path("/tmp/_app_test_teacher.hwpx")
    path.write_bytes(body)
    text = HwpxDocument.open(str(path)).export_text()
    assert "12생윤05-02" in text, "고친 성취기준이 한글 파일에 들어가야 한다"
    path.unlink(missing_ok=True)


def test_clearing_a_standard_marks_it_unmapped(client):
    data = client.post("/convert", data={
        "file": upload(SAMPLE),
        "standards": upload(STANDARDS),
    }, content_type="multipart/form-data").get_json()

    updated = client.post("/update", json={
        "token": data["token"], "standards": {"1": ""},
    }).get_json()
    assert 1 in updated["unmapped"]


# ---------- 잘못된 입력 ----------


def test_missing_file_is_reported(client):
    response = client.post("/convert", data={}, content_type="multipart/form-data")
    assert response.status_code == 400
    assert "파일" in response.get_json()["error"]


def test_wrong_extension_is_reported(client):
    response = client.post("/convert", data={
        "file": (io.BytesIO(b"hello"), "메모.txt"),
    }, content_type="multipart/form-data")
    assert response.status_code == 500
    assert "PDF" in response.get_json()["error"]


def test_broken_json_is_reported_not_crashed(client):
    response = client.post("/convert", data={
        "file": (io.BytesIO(b"{ this is not json "), "깨진.json"),
    }, content_type="multipart/form-data")
    assert response.status_code == 500
    assert response.get_json()["error"]


def test_unreadable_standards_file_is_warned_not_fatal(client):
    """성취기준 파일이 형식에 안 맞아도 변환 자체는 되어야 한다."""
    response = client.post("/convert", data={
        "file": upload(SAMPLE),
        "standards": (io.BytesIO("아무 설명글\n형식에 안 맞음\n".encode()), "메모.txt"),
    }, content_type="multipart/form-data")

    assert response.status_code == 200
    data = response.get_json()
    assert data["has_standards"] is False
    assert any("하나도 읽지 못했" in w for w in data["warnings"])


def test_unknown_token_is_reported(client):
    response = client.post("/update", json={"token": "없는토큰", "standards": {}})
    assert response.status_code == 404


def test_unknown_download_is_reported(client):
    assert client.get("/download/없는토큰/student").status_code == 404


# ---------- 성취기준을 붙여넣기 텍스트로 준 경우 ----------


def test_standards_from_pasted_text_file(client):
    pasted = (
        "[12생윤04-02] 인간과 자연의 관계를 다양한 관점에서 설명할 수 있다.\n"
        "[12생윤01-02] 윤리 이론을 통해 도덕적 판단을 내릴 수 있다.\n"
    )
    data = client.post("/convert", data={
        "file": upload(SAMPLE),
        "standards": (io.BytesIO(pasted.encode("utf-8")), "성취기준.txt"),
    }, content_type="multipart/form-data").get_json()

    assert data["standard_count"] == 2
    assert data["rows"][0]["standard"] == "12생윤04-02"
