"""ブラウザで使う切り抜きアプリ。`kirinuki-app` で起動し、URL を貼るだけで切り抜きを作る。

標準ライブラリだけで動くローカル専用サーバー (127.0.0.1)。
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from . import __version__
from .pipeline import KirinukiError, Options, run

MAX_LOG_LINES = 500


@dataclass
class Job:
    id: str
    opts: Options
    out_dir: Path
    status: str = "queued"  # queued / running / done / error
    logs: list[str] = field(default_factory=list)
    result: dict | None = None
    error: str | None = None
    created: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "id": self.id, "status": self.status, "source": self.opts.source,
            "logs": self.logs[-MAX_LOG_LINES:], "result": self.result, "error": self.error,
            "created": self.created, "out_dir": str(self.out_dir),
            "options": {
                "count": self.opts.count, "min_len": self.opts.min_len,
                "max_len": self.opts.max_len, "vertical": self.opts.vertical,
                "lead_in": self.opts.lead_in,
            },
        }


class JobManager:
    """ジョブを1本ずつ順番に処理する(重い処理を同時に走らせない)。"""

    def __init__(self, output_root: Path):
        self.output_root = output_root
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=1)

    def submit(self, opts: Options) -> Job:
        job_id = uuid.uuid4().hex[:10]
        out_dir = self.output_root / f"{time.strftime('%Y%m%d_%H%M%S')}_{job_id}"
        opts.output = str(out_dir)
        job = Job(job_id, opts, out_dir)
        with self.lock:
            self.jobs[job_id] = job
        self.executor.submit(self._run, job)
        return job

    def get(self, job_id: str) -> Job | None:
        with self.lock:
            return self.jobs.get(job_id)

    def list(self) -> list[Job]:
        with self.lock:
            return sorted(self.jobs.values(), key=lambda j: j.created, reverse=True)

    def _run(self, job: Job) -> None:
        def log(msg: str) -> None:
            with self.lock:
                job.logs.append(msg)

        job.status = "running"
        try:
            result = run(job.opts, log=log)
            job.result = result.to_dict(score_points=400)
            job.status = "done"
            log("完了しました")
        except KirinukiError as e:
            job.error = str(e)
            job.status = "error"
        except Exception as e:  # 予期しないエラーも画面に出す
            job.error = f"{type(e).__name__}: {e}"
            job.status = "error"
            log(traceback.format_exc())


def parse_options(data: dict) -> Options:
    """画面から送られた設定を検証して Options にする。"""
    source = str(data.get("source") or "").strip()
    if not source:
        raise ValueError("URL を入力してください")
    if not re.match(r"^https?://", source) and not Path(source).is_file():
        raise ValueError("YouTube の URL (https://...) を入力してください")

    def num(key: str, default: int, lo: int, hi: int) -> int:
        try:
            value = int(data.get(key, default))
        except (TypeError, ValueError):
            raise ValueError(f"{key} は数値で指定してください") from None
        if not lo <= value <= hi:
            raise ValueError(f"{key} は {lo}〜{hi} の範囲で指定してください")
        return value

    opts = Options(
        source=source,
        count=num("count", 3, 1, 20),
        min_len=num("min_len", 30, 5, 600),
        max_len=num("max_len", 60, 5, 600),
        lead_in=num("lead_in", 10, 0, 120),
        vertical=bool(data.get("vertical", False)),
    )
    if opts.min_len > opts.max_len:
        raise ValueError("最短秒数は最長秒数以下にしてください")
    return opts


def open_folder(path: Path) -> None:
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def make_handler(manager: JobManager, allowed_hosts: set[str]):
    index_html = (resources.files("kirinuki") / "web" / "index.html").read_bytes()

    class Handler(BaseHTTPRequestHandler):
        server_version = f"kirinuki/{__version__}"

        def log_message(self, format, *args):  # アクセスログは出さない
            pass

        # --- helpers -------------------------------------------------------
        def _host_ok(self) -> bool:
            # DNS rebinding 対策: localhost 以外の Host からのアクセスは拒否
            return self.headers.get("Host", "") in allowed_hosts

        def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, obj) -> None:
            self._send(status, json.dumps(obj, ensure_ascii=False).encode(),
                       "application/json; charset=utf-8")

        def _error(self, status: int, message: str) -> None:
            self._json(status, {"error": message})

        # --- routes --------------------------------------------------------
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            if not self._host_ok():
                return self._error(HTTPStatus.FORBIDDEN, "forbidden")
            path = urlparse(self.path).path
            if path == "/":
                return self._send(HTTPStatus.OK, index_html, "text/html; charset=utf-8")
            if path == "/api/jobs":
                return self._json(HTTPStatus.OK, [j.to_dict() for j in manager.list()])
            m = re.fullmatch(r"/api/jobs/([0-9a-f]+)", path)
            if m:
                job = manager.get(m.group(1))
                if not job:
                    return self._error(HTTPStatus.NOT_FOUND, "ジョブが見つかりません")
                return self._json(HTTPStatus.OK, job.to_dict())
            m = re.fullmatch(r"/files/([0-9a-f]+)/(.+)", path)
            if m:
                return self._serve_file(m.group(1), unquote(m.group(2)))
            self._error(HTTPStatus.NOT_FOUND, "not found")

        def do_POST(self):
            if not self._host_ok():
                return self._error(HTTPStatus.FORBIDDEN, "forbidden")
            # 他サイトからの送信 (CSRF) を防ぐため JSON 以外は受け付けない
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "JSON で送信してください")
            try:
                length = int(self.headers.get("Content-Length", 0))
                data = json.loads(self.rfile.read(min(length, 65536)) or b"{}")
            except (ValueError, json.JSONDecodeError):
                return self._error(HTTPStatus.BAD_REQUEST, "リクエストが不正です")

            path = urlparse(self.path).path
            if path == "/api/jobs":
                try:
                    opts = parse_options(data)
                except ValueError as e:
                    return self._error(HTTPStatus.BAD_REQUEST, str(e))
                job = manager.submit(opts)
                return self._json(HTTPStatus.CREATED, job.to_dict())
            m = re.fullmatch(r"/api/jobs/([0-9a-f]+)/open", path)
            if m:
                job = manager.get(m.group(1))
                if not job or not job.out_dir.exists():
                    return self._error(HTTPStatus.NOT_FOUND, "フォルダがありません")
                try:
                    open_folder(job.out_dir)
                except Exception as e:
                    return self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(e))
                return self._json(HTTPStatus.OK, {"ok": True})
            self._error(HTTPStatus.NOT_FOUND, "not found")

        def _serve_file(self, job_id: str, name: str):
            job = manager.get(job_id)
            files = {c.get("file") for c in (job.result or {}).get("clips", [])} if job else set()
            if not job or name not in files:  # ジョブの出力ファイル以外は返さない
                return self._error(HTTPStatus.NOT_FOUND, "ファイルが見つかりません")
            path = job.out_dir / name
            if not path.is_file():
                return self._error(HTTPStatus.NOT_FOUND, "ファイルが見つかりません")

            size = path.stat().st_size
            ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
            start, end = 0, size - 1
            status = HTTPStatus.OK
            # 動画のシーク用に Range リクエストに対応する
            rng = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
            if rng and (rng.group(1) or rng.group(2)):
                if rng.group(1):
                    start = int(rng.group(1))
                    end = min(int(rng.group(2)), size - 1) if rng.group(2) else size - 1
                else:
                    start = max(0, size - int(rng.group(2)))
                if start > end or start >= size:
                    self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                status = HTTPStatus.PARTIAL_CONTENT

            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(end - start + 1))
            if status == HTTPStatus.PARTIAL_CONTENT:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            if "download" in urlparse(self.path).query:
                self.send_header("Content-Disposition",
                                 f"attachment; filename*=UTF-8''{quote(name)}")
            self.end_headers()
            if self.command == "HEAD":
                return
            with open(path, "rb") as f:
                f.seek(start)
                remaining = end - start + 1
                try:
                    while remaining > 0:
                        chunk = f.read(min(1 << 16, remaining))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        remaining -= len(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass  # ブラウザがシークなどで接続を切るのは正常

    return Handler


def create_server(host: str, port: int, output_root: Path) -> ThreadingHTTPServer:
    manager = JobManager(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer((host, port), None)  # type: ignore[arg-type]
    actual_port = server.server_address[1]
    allowed = {f"{h}:{actual_port}" for h in ("127.0.0.1", "localhost", host)}
    server.RequestHandlerClass = make_handler(manager, allowed)
    server.manager = manager  # type: ignore[attr-defined]
    return server


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="kirinuki-app", description="切り抜きアプリを起動します。")
    p.add_argument("--port", type=int, default=8765, help="ポート番号 (既定: 8765)")
    p.add_argument("--host", default="127.0.0.1", help="待ち受けアドレス (既定: 127.0.0.1)")
    p.add_argument("-o", "--output", default=str(Path.home() / "kirinuki_clips"),
                   help="切り抜きの保存先 (既定: ~/kirinuki_clips)")
    p.add_argument("--no-browser", action="store_true", help="ブラウザを自動で開かない")
    args = p.parse_args(argv)

    try:
        server = create_server(args.host, args.port, Path(args.output).expanduser())
    except OSError as e:
        print(f"起動できませんでした (ポート {args.port} が使用中かもしれません): {e}", file=sys.stderr)
        return 1
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"切り抜きアプリを起動しました: {url}")
    print(f"保存先: {Path(args.output).expanduser().resolve()}")
    print("終了するには Ctrl+C を押してください")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n終了します")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
