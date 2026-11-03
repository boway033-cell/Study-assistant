"""scripts/maintenance/backup.py 的隔离测试（P1-3）。

所有用例只在 tmp_path 下构造虚构项目与数据目录，并通过 --project-root、
DATA_DIR 环境变量与 --data-dir 显式指认；子进程 cwd 与 TMP/TEMP 也被限制在
tmp_path 内，绝不读取、复制、压缩或校验仓库真实的 backend/data。
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "maintenance" / "backup.py"
BAT_SCRIPT = REPO_ROOT / "scripts" / "maintenance" / "backup.bat"
DB_NAME = "study.db"
SECRET_VALUE = "sk-fictional-test-key-must-not-leak"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _make_project(tmp_path: Path, name: str = "repo") -> Path:
    project = tmp_path / name
    project.mkdir()
    return project


def _create_database(db_path: Path) -> None:
    connection = sqlite3.connect(db_path)
    try:
        connection.execute("CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT)")
        connection.execute("INSERT INTO books (title) VALUES ('虚构教材')")
        connection.commit()
    finally:
        connection.close()


def _populate_data_dir(data_dir: Path) -> None:
    """创建与真实数据目录同构的虚构内容（含任意额外子目录）。"""
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "uploads").mkdir()
    (data_dir / "uploads" / "虚构资料.pdf").write_bytes(b"%PDF-1.4 fictional upload")
    (data_dir / "structured").mkdir()
    (data_dir / "structured" / "book.json").write_text('{"title": "虚构"}', encoding="utf-8")
    (data_dir / "presentations").mkdir()
    (data_dir / "presentations" / "deck.pptx").write_bytes(b"PK fictional pptx")
    (data_dir / "chroma").mkdir()
    (data_dir / "chroma" / "collection.bin").write_bytes(b"chroma fake")
    (data_dir / "models").mkdir()
    (data_dir / "models" / "embedding.onnx").write_bytes(b"model fake")
    (data_dir / "runtime").mkdir()
    (data_dir / "runtime" / "launcher.log").write_text("started\n", encoding="utf-8")
    (data_dir / "writing").mkdir()
    (data_dir / "writing" / "draft.md").write_text("# 虚构草稿\n", encoding="utf-8")
    (data_dir / "custom-extra").mkdir()
    (data_dir / "custom-extra" / "notes.txt").write_text("任意子目录也应保留", encoding="utf-8")
    _create_database(data_dir / DB_NAME)


def _run_backup(
    project: Path,
    *,
    data_dir: str | Path | None = None,
    env_data_dir: str | None = None,
    extra_args: list[str] | None = None,
    port: int | None = None,
) -> subprocess.CompletedProcess[str]:
    work = project / "_work"
    work.mkdir(exist_ok=True)
    temp = project / "_temp"
    temp.mkdir(exist_ok=True)
    args = [
        sys.executable,
        str(SCRIPT),
        "--project-root",
        str(project),
        "--work-dir",
        str(work),
        "--port",
        str(port if port is not None else _free_port()),
    ]
    if data_dir is not None:
        args += ["--data-dir", str(data_dir)]
    args += list(extra_args or [])
    env = {key: value for key, value in os.environ.items() if key != "DATA_DIR"}
    env.update({"TMP": str(temp), "TEMP": str(temp), "PYTHONIOENCODING": "utf-8"})
    if env_data_dir is not None:
        env["DATA_DIR"] = env_data_dir
    return subprocess.run(
        args,
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def _run_bat(
    project: Path,
    *,
    env_data_dir: str | Path | None = None,
    extra_args: list[str] | None = None,
    port: int | None = None,
) -> subprocess.CompletedProcess[str]:
    """通过真实 cmd.exe 启动 backup.bat，验证启动器透传参数与退出码。"""
    if os.name != "nt" or shutil.which("cmd") is None:
        pytest.skip("backup.bat 只在 Windows cmd 环境下可测")
    work = project / "_work"
    work.mkdir(exist_ok=True)
    temp = project / "_temp"
    temp.mkdir(exist_ok=True)
    args = [
        os.environ.get("COMSPEC", "cmd.exe"),
        "/c",
        str(BAT_SCRIPT),
        "--no-pause",
        "--project-root",
        str(project),
        "--work-dir",
        str(work),
        "--port",
        str(port if port is not None else _free_port()),
    ]
    args += list(extra_args or [])
    env = {key: value for key, value in os.environ.items() if key != "DATA_DIR"}
    env.update({"TMP": str(temp), "TEMP": str(temp), "PYTHONIOENCODING": "utf-8"})
    if env_data_dir is not None:
        env["DATA_DIR"] = str(env_data_dir)
    return subprocess.run(
        args,
        cwd=str(project),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def _only_zip(project: Path) -> Path:
    zips = sorted(project.glob("backup-*.zip"))
    assert len(zips) == 1, f"应恰好生成一个备份包，实际：{[p.name for p in zips]}"
    return zips[0]


def _extract(archive_path: Path, target: Path, member: str) -> Path:
    with zipfile.ZipFile(archive_path) as archive:
        archive.extract(member, target)
    return target / member


def _assert_database_ok(db_path: Path) -> list[tuple[str]]:
    connection = sqlite3.connect(db_path)
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchall()
        assert [row[0] for row in integrity] == ["ok"]
        count = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchone()[0]
        assert count > 0
        return connection.execute("SELECT title FROM books ORDER BY id").fetchall()
    finally:
        connection.close()


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        if self.path.split("?", 1)[0] != "/api/health":
            self.send_error(404)
            return
        body = json.dumps(
            {"status": "ok", "app": "study-assistant", "api_revision": 15}
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - 保持静默
        return


@contextlib.contextmanager
def _fake_health_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_address[1])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_default_data_dir_resolved_relative_to_project_root(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    data_dir = project / "backend" / "data"
    _populate_data_dir(data_dir)

    result = _run_backup(project)

    assert result.returncode == 0, result.stderr
    assert f"来源: {data_dir.resolve()}" in result.stdout
    assert "解析: 默认值 backend/data" in result.stdout
    assert "目标: " in result.stdout
    target_line = next(line for line in result.stdout.splitlines() if line.startswith("目标: "))
    assert Path(target_line.split(": ", 1)[1]).is_absolute()
    archive = _only_zip(project)
    with zipfile.ZipFile(archive) as zf:
        assert DB_NAME in zf.namelist()
    _assert_database_ok(_extract(archive, tmp_path / "recovered-default", DB_NAME))


def test_env_data_dir_absolute_is_used(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    data_dir = tmp_path / "custom-absolute"
    _populate_data_dir(data_dir)

    result = _run_backup(project, env_data_dir=str(data_dir))

    assert result.returncode == 0, result.stderr
    assert f"来源: {data_dir.resolve()}" in result.stdout
    assert "解析: 环境变量 DATA_DIR" in result.stdout
    _assert_database_ok(_extract(_only_zip(project), tmp_path / "recovered-abs", DB_NAME))


def test_env_data_dir_relative_resolved_against_project_root(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    data_dir = project / "custom-data"
    _populate_data_dir(data_dir)

    result = _run_backup(project, env_data_dir="custom-data")

    assert result.returncode == 0, result.stderr
    assert f"来源: {data_dir.resolve()}" in result.stdout
    assert _only_zip(project).exists()


def test_dotenv_data_dir_used_and_secret_never_leaks(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    dotenv_dir = project / "dotenv-data"
    _populate_data_dir(dotenv_dir)
    (project / ".env").write_text(
        f"DATA_DIR=dotenv-data\nDEEPSEEK_API_KEY={SECRET_VALUE}\n", encoding="utf-8"
    )

    result = _run_backup(project)

    assert result.returncode == 0, result.stderr
    assert f"来源: {dotenv_dir.resolve()}" in result.stdout
    assert "解析:" in result.stdout and ".env" in result.stdout
    assert SECRET_VALUE not in result.stdout
    assert SECRET_VALUE not in result.stderr
    archive = _only_zip(project)
    with zipfile.ZipFile(archive) as zf:
        assert not any(name.endswith(".env") for name in zf.namelist())
    _assert_database_ok(_extract(archive, tmp_path / "recovered-dotenv", DB_NAME))


def test_env_data_dir_takes_priority_over_dotenv(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    env_dir = project / "env-data"
    dotenv_dir = project / "dotenv-data"
    _populate_data_dir(env_dir)
    _populate_data_dir(dotenv_dir)
    (env_dir / "marker-env.txt").write_text("env", encoding="utf-8")
    (dotenv_dir / "marker-dotenv.txt").write_text("dotenv", encoding="utf-8")
    (project / ".env").write_text("DATA_DIR=dotenv-data\n", encoding="utf-8")

    result = _run_backup(project, env_data_dir=str(env_dir))

    assert result.returncode == 0, result.stderr
    assert f"来源: {env_dir.resolve()}" in result.stdout
    with zipfile.ZipFile(_only_zip(project)) as zf:
        names = zf.namelist()
    assert "marker-env.txt" in names
    assert "marker-dotenv.txt" not in names


def test_cli_data_dir_overrides_env(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    env_dir = tmp_path / "env-data"
    cli_dir = tmp_path / "cli-data"
    _populate_data_dir(env_dir)
    _populate_data_dir(cli_dir)
    (env_dir / "marker-env.txt").write_text("env", encoding="utf-8")
    (cli_dir / "marker-cli.txt").write_text("cli", encoding="utf-8")

    result = _run_backup(project, data_dir=cli_dir, env_data_dir=str(env_dir))

    assert result.returncode == 0, result.stderr
    assert f"来源: {cli_dir.resolve()}" in result.stdout
    assert "解析: 命令行 --data-dir" in result.stdout
    with zipfile.ZipFile(_only_zip(project)) as zf:
        names = zf.namelist()
    assert "marker-cli.txt" in names
    assert "marker-env.txt" not in names


def test_missing_data_dir_fails_without_success_message(tmp_path: Path) -> None:
    project = _make_project(tmp_path)

    result = _run_backup(project)

    assert result.returncode != 0
    assert "数据目录不存在" in result.stderr
    assert "成功" not in result.stdout
    assert not list(project.glob("backup-*.zip"))


def test_missing_database_fails(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    data_dir = project / "backend" / "data"
    data_dir.mkdir(parents=True)
    (data_dir / "uploads").mkdir()

    result = _run_backup(project)

    assert result.returncode != 0
    assert "缺少 study.db" in result.stderr
    assert not list(project.glob("backup-*.zip"))


def test_data_dir_equal_project_root_is_rejected(tmp_path: Path) -> None:
    project = _make_project(tmp_path)

    result = _run_backup(project, data_dir=project)

    assert result.returncode != 0
    assert "拒绝备份整个项目" in result.stderr
    assert not list(project.glob("backup-*.zip"))


def test_archive_is_verifiable_and_contains_recoverable_data(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    data_dir = project / "backend" / "data"
    _populate_data_dir(data_dir)
    (data_dir / ".env").write_text(f"DEEPSEEK_API_KEY={SECRET_VALUE}\n", encoding="utf-8")

    result = _run_backup(project)

    assert result.returncode == 0, result.stderr
    assert "[OK] 备份成功" in result.stdout
    assert "integrity_check=ok" in result.stdout
    assert SECRET_VALUE not in result.stdout + result.stderr
    archive = _only_zip(project)
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        assert DB_NAME in names
        assert names[0] == DB_NAME, "备份包内文件应位于根目录"
        assert ".env" not in names
        assert not any(name.startswith(".env.") for name in names)
        assert not any("study.db-" in name for name in names)
        for expected in (
            "uploads/虚构资料.pdf",
            "structured/book.json",
            "presentations/deck.pptx",
            "chroma/collection.bin",
            "models/embedding.onnx",
            "runtime/launcher.log",
            "writing/draft.md",
            "custom-extra/notes.txt",
        ):
            assert expected in names, f"缺少备份成员：{expected}"

    recovered = tmp_path / "recovered"
    recovered_db = _extract(archive, recovered, DB_NAME)
    titles = _assert_database_ok(recovered_db)
    assert ("虚构教材",) in titles

    recovered_pdf = _extract(archive, recovered, "uploads/虚构资料.pdf")
    assert recovered_pdf.read_bytes() == (data_dir / "uploads" / "虚构资料.pdf").read_bytes()


def test_wal_committed_rows_are_in_snapshot(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    data_dir = project / "wal-data"
    data_dir.mkdir(parents=True)
    database = data_dir / DB_NAME
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT)")
        connection.execute("INSERT INTO books (title) VALUES ('已检查点内容')")
        connection.commit()
        connection.execute("INSERT INTO books (title) VALUES ('WAL 中已提交内容')")
        connection.commit()
        wal_file = data_dir / f"{DB_NAME}-wal"
        assert wal_file.exists() and wal_file.stat().st_size > 0

        result = _run_backup(project, data_dir=data_dir)
    finally:
        connection.close()

    assert result.returncode == 0, result.stderr
    archive = _only_zip(project)
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
    assert not any(name.startswith(f"{DB_NAME}-") for name in names)
    recovered_db = _extract(archive, tmp_path / "recovered-wal", DB_NAME)
    titles = _assert_database_ok(recovered_db)
    assert ("已检查点内容",) in titles
    assert ("WAL 中已提交内容",) in titles, "WAL 中已提交内容必须包含在一致性快照里"


def test_running_service_detected_by_http_health(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    data_dir = project / "backend" / "data"
    _populate_data_dir(data_dir)

    with _fake_health_server() as port:
        blocked = _run_backup(project, port=port)
        assert blocked.returncode != 0
        assert "检测到学习助手服务可能仍在运行" in blocked.stderr
        assert "stop.bat" in blocked.stderr
        assert "成功" not in blocked.stdout
        assert not list(project.glob("backup-*.zip"))

        allowed = _run_backup(project, port=port, extra_args=["--allow-online"])
        assert allowed.returncode == 0, allowed.stderr
        assert "[警告]" in allowed.stdout
        assert "服务似乎仍在运行" in allowed.stdout

    archive = _only_zip(project)
    _assert_database_ok(_extract(archive, tmp_path / "recovered-online", DB_NAME))


def test_running_service_detected_by_pid_file(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    data_dir = project / "backend" / "data"
    _populate_data_dir(data_dir)
    (project / "server.pid").write_text(str(os.getpid()), encoding="utf-8")

    blocked = _run_backup(project)
    assert blocked.returncode != 0
    assert "PID" in blocked.stderr
    assert not list(project.glob("backup-*.zip"))

    allowed = _run_backup(project, extra_args=["--allow-online"])
    assert allowed.returncode == 0, allowed.stderr
    assert "服务似乎仍在运行" in allowed.stdout


def test_batch_launcher_help_exits_zero_without_pause(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    result = _run_bat(project, extra_args=["--help"])
    assert result.returncode == 0, result.stderr
    assert "usage: backup.py" in result.stdout


def test_batch_launcher_backs_up_and_passes_exit_code(tmp_path: Path) -> None:
    project = _make_project(tmp_path)
    data_dir = project / "backend" / "data"
    _populate_data_dir(data_dir)

    success = _run_bat(project, env_data_dir=data_dir)
    assert success.returncode == 0, success.stderr
    assert "[OK] 备份成功" in success.stdout
    archive = _only_zip(project)
    _assert_database_ok(_extract(archive, tmp_path / "recovered-bat", DB_NAME))

    missing = _run_bat(
        project,
        env_data_dir=project / "does-not-exist",
        extra_args=["--quiet"],
    )
    assert missing.returncode == 1
    assert "成功" not in missing.stdout
    assert "数据目录不存在" in missing.stderr
    _assert_database_ok(_extract(_only_zip(project), tmp_path / "recovered-pid", DB_NAME))
