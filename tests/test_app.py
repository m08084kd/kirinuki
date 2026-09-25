"""Web アプリの API テスト(実際にサーバーを立ち上げて叩く)。"""

import http.client
import json
import threading
import time

import pytest

from kirinuki import app


@pytest.fixture()
def server(tmp_path):
    srv = app.create_server("127.0.0.1", 0, tmp_path / "out")
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def request(srv, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_address[1], timeout=10)
    hdrs = {"Host": f"127.0.0.1:{srv.server_address[1]}", **(headers or {})}
    if body is not None and not isinstance(body, (bytes, str)):
        body = json.dumps(body)
        hdrs.setdefault("Content-Type", "application/json")
    conn.request(method, path, body=body, headers=hdrs)
    res = conn.getresponse()
    data = res.read()
    conn.close()
    return res, data


def test_index_page(server):
    res, data = request(server, "GET", "/")
    assert res.status == 200
    assert "kirinuki" in data.decode()


def test_rejects_foreign_host(server):
    res, _ = request(server, "GET", "/api/jobs", headers={"Host": "evil.example:80"})
    assert res.status == 403


def test_rejects_non_json_post(server):
    res, _ = request(server, "POST", "/api/jobs", body="source=x",
                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert res.status == 415


@pytest.mark.parametrize("payload, message", [
    ({"source": ""}, "URL"),
    ({"source": "not a url"}, "URL"),
    ({"source": "https://youtu.be/x", "min_len": 90, "max_len": 30}, "最短"),
    ({"source": "https://youtu.be/x", "count": 0}, "count"),
])
def test_validation(server, payload, message):
    res, data = request(server, "POST", "/api/jobs", body=payload)
    assert res.status == 400
    assert message in json.loads(data)["error"]


def test_job_end_to_end(server, sample_video):
    res, data = request(server, "POST", "/api/jobs",
                        body={"source": str(sample_video), "count": 1})
    assert res.status == 201
    job_id = json.loads(data)["id"]

    for _ in range(120):
        _, data = request(server, "GET", f"/api/jobs/{job_id}")
        job = json.loads(data)
        if job["status"] in ("done", "error"):
            break
        time.sleep(0.5)
    assert job["status"] == "done", job
    (clip,) = job["result"]["clips"]
    assert clip["start"] <= 100 and clip["end"] >= 110
    assert job["result"]["score"]

    # 動画ファイルの配信 (シーク用の Range 対応)
    path = f"/files/{job_id}/{clip['file']}"
    res, body = request(server, "GET", path, headers={"Range": "bytes=0-99"})
    assert res.status == 206 and len(body) == 100
    res, _ = request(server, "GET", path + "?download=1")
    assert res.status == 200 and "attachment" in res.getheader("Content-Disposition")

    # ジョブの出力以外は取れない
    res, _ = request(server, "GET", f"/files/{job_id}/..%2F..%2Fetc%2Fpasswd")
    assert res.status == 404
