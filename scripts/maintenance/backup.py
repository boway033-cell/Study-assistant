#!/usr/bin/env python3
"""学习助手数据备份脚本（P1-3）。

按应用同一规则解析实际数据目录（DATA_DIR）：
  1. 命令行 --data-dir（最高优先级，便于测试和诊断）
  2. 环境变量 DATA_DIR
  3. 项目根目录 .env 中的 DATA_DIR
  4. 默认 backend/data
相对路径以项目根目录（仓库根）为基准，与 backend/app/core/config.py 的
settings.data_dir 解析规则一致（config.py:71-76）。

本脚本刻意不 import backend.app.core.config：
  * 导入 settings 会触发 _ensure_dirs()，在源目录不存在时静默创建目录，
    而备份工具必须在“目录不存在”时失败，不能伪造成功；
  * 也避免为了一个备份动作初始化数据库/依赖。
因此这里独立实现同一套规则；修改 config 的解析规则时需要同步本文件。

安全行为：
  * 执行前打印实际来源目录和绝对目标路径；
  * 默认拒绝停服检查发现仍在运行的服务（HTTP /api/health、server.pid），
    要求先安全停止服务；--allow-online 可显式跳过，但仍用 SQLite 一致性备份；
  * study.db 使用 sqlite3 backup() 生成一致性快照（包含 WAL 中已提交内容），
    不打包原始 -wal/-shm/-journal；
  * 其余数据目录内容复制到临时 staging 后再压缩，避免压缩期间源文件变化；
  * 压缩后重新打开 ZIP，校验成员齐全、study.db 可解压且 PRAGMA integrity_check=ok；
  * 不读取/输出 .env 或其它密钥内容；.env 文件永远不进入备份包。

退出码：0 成功；1 失败（目录不存在、服务运行中、复制/压缩/校验失败等）。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import quote, urlsplit

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DATA_DIR_RELATIVE = "backend/data"
DB_NAME = "study.db"
# 原始 SQLite 旁路文件不进入备份包：快照已经包含已提交事务。
SQLITE_SIDECAR_PREFIX = "study.db-"
SQLITE_SIDECAR_NAMES = {"study.db-wal", "study.db-shm", "study.db-journal"}
# 不打包 .env。数据目录的 .secret_key 用于恢复数据库中的加密配置。
SECRET_NAMES = {".env"}
SECRET_PREFIXES = (".env.",)
DEFAULT_HEALTH_PORTS = tuple(range(8000, 8011))
EXIT_FAILURE = 1


class BackupError(RuntimeError):
    """可预期的备份失败（打印错误并返回非零退出码）。"""


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} TB"


def _is_secret_name(name: str) -> bool:
    if name in SECRET_NAMES:
        return True
    return any(name.startswith(prefix) for prefix in SECRET_PREFIXES)


def _is_sqlite_sidecar(name: str) -> bool:
    return name in SQLITE_SIDECAR_NAMES or name.startswith(SQLITE_SIDECAR_PREFIX)


def _read_dotenv_setting(env_file: Path, name: str) -> str | None:
    """使用与 load_dotenv 相同的解析和插值规则，不修改进程环境。"""
    from dotenv.main import DotEnv

    # load_dotenv 默认 override=False：插值时现有环境变量优先。
    # DotEnv.get 不向 os.environ 写入任何值，也不输出其他配置。
    return DotEnv(env_file, override=False, encoding="utf-8").get(name)


def resolve_data_dir(data_dir_arg: str | None, project_root: Path) -> tuple[Path, str]:
    """返回 (绝对数据目录, 解析来源说明)，规则与 config.py 保持一致。"""
    env_value = os.environ.get("DATA_DIR")
    env_file = project_root / ".env"
    if data_dir_arg is not None:
        raw, source = data_dir_arg, "命令行 --data-dir"
    elif env_value is not None:
        raw, source = env_value, "环境变量 DATA_DIR"
    else:
        dotenv_value = _read_dotenv_setting(env_file, "DATA_DIR")
        if dotenv_value is not None:
            raw, source = dotenv_value, f"{env_file} 中的 DATA_DIR"
        else:
            raw, source = DEFAULT_DATA_DIR_RELATIVE, "默认值 backend/data"
    if raw is None:
        raw = ""
    raw = str(raw)
    path = Path(raw) if raw else Path(".")
    if not path.is_absolute():
        path = project_root / path
    return path.resolve(), source


def _parse_ports(values: list[str] | None) -> list[int]:
    ports: list[int] = []
    for value in values or []:
        for piece in str(value).split(","):
            piece = piece.strip()
            if not piece:
                continue
            try:
                port = int(piece)
            except ValueError as exc:
                raise BackupError(f"无效端口：{piece}") from exc
            if not 1 <= port <= 65535:
                raise BackupError(f"端口超出范围：{port}")
            if port not in ports:
                ports.append(port)
    return ports


def _runtime_url_ports(data_dir: Path) -> list[int]:
    """读取数据目录 runtime/server.url 中记录的端口（启动器写入）。"""
    url_file = data_dir / "runtime" / "server.url"
    try:
        text = url_file.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return []
    parts = urlsplit(text)
    try:
        port = parts.port
    except ValueError:
        return []
    if parts.scheme != "http" or port is None:
        return []
    if parts.hostname not in {"127.0.0.1", "localhost", "::1"}:
        return []
    return [port]


def _health_ok(port: int, timeout: float) -> bool:
    """短超时探测本应用健康接口；任何异常都视为未运行。"""
    url = f"http://127.0.0.1:{port}/api/health"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310 - 只访问回环地址
            if response.status != 200:
                return False
            payload = json.loads(response.read(4096).decode("utf-8", errors="replace"))
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
        return False
    return isinstance(payload, dict) and payload.get("status") == "ok" and payload.get("app") == "study-assistant"


def _pid_alive(pid: int) -> bool:
    """判断 PID 是否仍存在；Windows 使用 OpenProcess，避免 os.kill 的终止副作用。"""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
            if not handle:
                return False
            try:
                exit_code = ctypes.c_ulong()
                if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return True
                return exit_code.value == 259  # STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        except (OSError, AttributeError):
            return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_pid_file(path: Path) -> int | None:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    if not lines:
        return None
    raw = lines[0].strip()
    return int(raw) if raw.isdigit() else None


def detect_running_service(project_root: Path, data_dir: Path, ports: list[int], timeout: float) -> list[str]:
    """返回服务仍在运行的证据列表（空列表表示未发现运行中的服务）。"""
    evidence: list[str] = []
    for port in ports:
        if _health_ok(port, timeout):
            evidence.append(f"127.0.0.1:{port} 的 /api/health 仍返回本应用健康响应")

    pid_candidates = []
    root_pid = _read_pid_file(project_root / "server.pid")
    if root_pid:
        pid_candidates.append((str(project_root / "server.pid"), root_pid))
    for lock_name in ("server.pid", "server.lock"):
        lock_pid = _read_pid_file(data_dir / "runtime" / lock_name)
        if lock_pid:
            pid_candidates.append((str(data_dir / "runtime" / lock_name), lock_pid))
    seen: set[int] = set()
    for path, pid in pid_candidates:
        if pid in seen:
            continue
        seen.add(pid)
        if _pid_alive(pid):
            evidence.append(f"{path} 指向仍在运行的进程（PID {pid}）")
    return evidence


def _find_service_ports(args: argparse.Namespace, data_dir: Path, project_root: Path) -> list[int]:
    explicit = _parse_ports(args.port)
    if explicit:
        return explicit
    configured = os.environ.get("PORT")
    if configured is None:
        try:
            configured = _read_dotenv_setting(project_root / ".env", "PORT")
        except (ImportError, OSError, UnicodeError) as exc:
            raise BackupError("无法读取 .env 中的服务端口，请检查安装与配置。") from exc
    configured_ports = _parse_ports([configured]) if configured else []
    return list(dict.fromkeys([*configured_ports, *_runtime_url_ports(data_dir), *DEFAULT_HEALTH_PORTS]))


def _snapshot_database(source: Path, destination: Path) -> None:
    """用 SQLite backup API 生成一致性快照（包含 WAL 中已提交内容）。"""
    uri = source.as_uri() + "?mode=ro"
    source_conn = None
    try:
        try:
            source_conn = sqlite3.connect(uri, uri=True, timeout=30)
        except sqlite3.Error:
            # 极少数只读 WAL 打开失败（例如 -shm 不可创建）时退回普通连接；
            # 仍通过 backup() 生成快照，不直接复制数据库文件。
            print("[提示] 只读方式打开 study.db 失败，改用普通连接执行 SQLite 一致性备份。")
            source_conn = sqlite3.connect(str(source), timeout=30)
        destination_conn = sqlite3.connect(str(destination))
        try:
            source_conn.backup(destination_conn)
            destination_conn.commit()
        finally:
            destination_conn.close()
    except sqlite3.Error as exc:
        raise BackupError(f"SQLite 一致性备份失败：{source}（{exc}）") from exc
    finally:
        if source_conn is not None:
            source_conn.close()


def _copy_attachments(data_dir: Path, staging: Path) -> tuple[int, int, list[str]]:
    """复制数据目录中 study.db 以外的内容；返回(文件数, 目录数, 跳过列表)。"""
    file_count = 0
    dir_count = 0
    skipped: list[str] = []
    try:
        children = sorted(data_dir.iterdir(), key=lambda p: p.name)
    except OSError as exc:
        raise BackupError(f"读取数据目录失败：{data_dir}（{exc}）") from exc
    for child in children:
        name = child.name
        if name == DB_NAME:
            continue
        if _is_sqlite_sidecar(name):
            skipped.append(name)
            continue
        if _is_secret_name(name):
            skipped.append(name)
            continue
        if child.is_symlink():
            skipped.append(f"{name}（符号链接）")
            continue
        destination = staging / name
        try:
            if child.is_dir():
                shutil.copytree(
                    child,
                    destination,
                    symlinks=True,
                    ignore=shutil.ignore_patterns(*SECRET_NAMES, *(prefix + "*" for prefix in SECRET_PREFIXES)),
                )
                dir_count += 1
                for _, dirnames, filenames in os.walk(destination):
                    dir_count += len(dirnames)
                    file_count += len(filenames)
            elif child.is_file():
                shutil.copy2(child, destination)
                file_count += 1
            else:
                skipped.append(f"{name}（特殊文件）")
        except OSError as exc:
            raise BackupError(f"复制 {child} 失败（{exc}）") from exc
    return file_count, dir_count, skipped


def _zip_staging(staging: Path, target: Path) -> int:
    """把 staging 内容放到压缩包根部；空目录也写入目录条目。"""
    written = 0
    try:
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as archive:
            for root, dirnames, filenames in os.walk(staging):
                dirnames.sort()
                root_path = Path(root)
                relative_root = root_path.relative_to(staging)
                if relative_root.parts:
                    archive.write(root_path, relative_root.as_posix() + "/")
                    written += 1
                for filename in sorted(filenames):
                    source_file = root_path / filename
                    if source_file.is_symlink():
                        print(f"[警告] 跳过符号链接：{source_file}")
                        continue
                    if _is_secret_name(filename):
                        print(f"[警告] 跳过密钥文件：{source_file}")
                        continue
                    archive.write(source_file, (relative_root / filename).as_posix())
                    written += 1
    except OSError as exc:
        raise BackupError(f"创建压缩包失败：{target}（{exc}）") from exc
    return written


def _expected_members(staging: Path) -> tuple[set[str], set[str]]:
    files: set[str] = set()
    dirs: set[str] = set()
    for root, dirnames, filenames in os.walk(staging):
        root_path = Path(root)
        relative_root = root_path.relative_to(staging)
        if relative_root.parts:
            dirs.add(relative_root.as_posix() + "/")
        for filename in filenames:
            # 与 _zip_staging 保持一致：符号链接不会进包，校验也不应期待它。
            if (root_path / filename).is_symlink():
                continue
            files.add((relative_root / filename).as_posix())
    return files, dirs


def verify_archive(archive_path: Path, staging: Path) -> dict[str, int]:
    """校验 ZIP 可打开、成员齐全、study.db 可恢复且完整。"""
    expected_files, expected_dirs = _expected_members(staging)
    try:
        with zipfile.ZipFile(archive_path) as archive:
            bad_member = archive.testzip()
            if bad_member:
                raise BackupError(f"压缩包成员 CRC 校验失败：{bad_member}")
            names = archive.namelist()
            actual_files = {name for name in names if not name.endswith("/")}
            missing_files = sorted(expected_files - actual_files)
            if missing_files:
                preview = "、".join(missing_files[:5])
                raise BackupError(f"压缩包缺少备份文件：{preview}（共 {len(missing_files)} 个）")
            missing_dirs = sorted(expected_dirs - set(names))
            if missing_dirs:
                preview = "、".join(missing_dirs[:5])
                raise BackupError(f"压缩包缺少目录条目：{preview}（共 {len(missing_dirs)} 个）")
            if DB_NAME not in actual_files:
                raise BackupError(f"压缩包缺少必需成员 {DB_NAME}")
            unsafe = []
            for name in names:
                normalized = name.replace("\\", "/")
                if normalized.startswith("/") or ".." in normalized.split("/"):
                    unsafe.append(name)
                if _is_secret_name(Path(normalized).name):
                    unsafe.append(name)
                if _is_sqlite_sidecar(Path(normalized).name):
                    unsafe.append(name)
            if unsafe:
                raise BackupError(f"压缩包包含不允许的成员：{'、'.join(sorted(set(unsafe))[:5])}")
            with tempfile.TemporaryDirectory(prefix="study-assistant-verify-") as verify_dir:
                archive.extract(DB_NAME, verify_dir)
                extracted_db = Path(verify_dir) / DB_NAME
                try:
                    connection = sqlite3.connect(str(extracted_db))
                    try:
                        rows = [row[0] for row in connection.execute("PRAGMA integrity_check").fetchall()]
                        table_count = connection.execute(
                            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                        ).fetchone()[0]
                    finally:
                        connection.close()
                except sqlite3.Error as exc:
                    raise BackupError(f"解压后的 {DB_NAME} 无法打开：{exc}") from exc
                db_size = extracted_db.stat().st_size
            if rows != ["ok"]:
                raise BackupError(f"解压后的 {DB_NAME} PRAGMA integrity_check 未通过：{rows}")
            if table_count <= 0:
                raise BackupError(f"解压后的 {DB_NAME} 不含任何数据表")
    except zipfile.BadZipFile as exc:
        raise BackupError(f"压缩包无法打开：{archive_path}（{exc}）") from exc
    return {
        "members": len(actual_files),
        "tables": table_count,
        "db_bytes": db_size,
        "archive_bytes": archive_path.stat().st_size,
    }


def _unique_target(target: Path) -> Path:
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for index in range(1, 1000):
        candidate = target.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise BackupError(f"无法确定可用的目标文件名：{target}")


def _resolve_target(args: argparse.Namespace, project_root: Path) -> Path:
    default_name = f"backup-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    if args.output:
        raw = Path(args.output)
        target = raw if raw.is_absolute() else (Path.cwd() / raw)
        target = target.resolve()
    else:
        target = (project_root / default_name).resolve()
    if target.is_dir():
        target = target / default_name
    return target


def _estimate_size(data_dir: Path) -> int:
    """估算数据目录大小（排除 SQLite 旁路文件），仅用于提前提示空间不足。"""
    total = 0
    for root, dirnames, filenames in os.walk(data_dir):
        dirnames[:] = [name for name in dirnames if not _is_secret_name(name)]
        for filename in filenames:
            if filename == DB_NAME or _is_sqlite_sidecar(filename) or _is_secret_name(filename):
                continue
            try:
                total += (Path(root) / filename).stat().st_size
            except OSError:
                continue
    try:
        total += (data_dir / DB_NAME).stat().st_size
    except OSError:
        pass
    return total


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="按实际 DATA_DIR 备份学习助手数据，并校验压缩包可恢复。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--data-dir", help="临时覆盖数据目录（相对路径按项目根解析；默认遵循 DATA_DIR 配置）")
    parser.add_argument("--output", help="备份 zip 路径（相对路径按当前工作目录解析）")
    parser.add_argument("--work-dir", help="临时 staging 目录的父目录（默认使用系统临时目录）")
    parser.add_argument(
        "--project-root",
        help="高级选项：覆盖项目根目录定位（.env 与相对路径基准），供测试/诊断使用",
    )
    parser.add_argument("--port", action="append", help="覆盖健康检查端口，可重复或逗号分隔；默认检查配置的 PORT、runtime/server.url 和 8000-8010")
    parser.add_argument("--health-timeout", type=float, default=0.35, help="单次健康检查超时（秒）")
    parser.add_argument("--allow-online", action="store_true", help="服务运行时仍继续备份（study.db 仍用 SQLite 一致性备份，附件为在线复制）")
    parser.add_argument("--quiet", action="store_true", help="减少过程输出（成功摘要与错误仍会输出）")
    parser.add_argument("--no-pause", action="store_true", help=argparse.SUPPRESS)  # backup.bat 自动化参数，脚本本身不会 pause
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    project_root = Path(args.project_root).resolve() if args.project_root else REPO_ROOT
    if not project_root.is_dir():
        print(f"[错误] 项目根目录不存在：{project_root}", file=sys.stderr)
        return EXIT_FAILURE

    try:
        data_dir, source = resolve_data_dir(args.data_dir, project_root)
    except ImportError:
        print("[错误] 无法解析 .env：缺少 python-dotenv。请先完成安装或使用项目的虚拟环境。", file=sys.stderr)
        return EXIT_FAILURE
    except (OSError, UnicodeError):
        print("[错误] 无法读取 .env，请检查文件权限和 UTF-8 编码。", file=sys.stderr)
        return EXIT_FAILURE
    target = _unique_target(_resolve_target(args, project_root))
    print(f"来源: {data_dir}")
    print(f"目标: {target}")
    print(f"解析: {source}")

    try:
        if data_dir == project_root:
            raise BackupError(
                "DATA_DIR 解析为项目根目录，拒绝备份整个项目。"
                "请把 DATA_DIR 指向专门的数据目录（默认 backend/data），并避免设置空的 DATA_DIR。"
            )
        if not data_dir.is_dir():
            raise BackupError(
                f"数据目录不存在：{data_dir}\n"
                f"        解析来源：{source}\n"
                "        请检查 .env / 环境变量 DATA_DIR 是否指向正确位置；目录不存在时备份会失败，不会报告成功。"
            )
        database = data_dir / DB_NAME
        if not database.is_file():
            raise BackupError(
                f"数据目录中缺少 {DB_NAME}：{data_dir}\n"
                "        目录可能不是学习助手的数据目录，或应用从未在该目录启动过。"
            )

        ports = _find_service_ports(args, data_dir, project_root)
        evidence = detect_running_service(project_root, data_dir, ports, max(0.05, args.health_timeout))
        if evidence and not args.allow_online:
            print("[错误] 检测到学习助手服务可能仍在运行，默认拒绝备份：", file=sys.stderr)
            for item in evidence:
                print(f"  - {item}", file=sys.stderr)
            print("请先运行 stop.bat（或关闭学习助手），确认服务完全停止后重试。", file=sys.stderr)
            print("如已确认可接受在线备份风险，可加 --allow-online；study.db 仍会用 SQLite 一致性备份。", file=sys.stderr)
            return EXIT_FAILURE
        if evidence:
            print("[警告] 服务似乎仍在运行，已按 --allow-online 继续：")
            for item in evidence:
                print(f"  - {item}")
            print("[警告] study.db 使用 SQLite 一致性备份；uploads/、structured/ 等附件为在线复制，可能捕获正在写入的文件。")
        elif not args.quiet:
            print("停服检查: 未发现运行中的学习助手服务")

        target.parent.mkdir(parents=True, exist_ok=True)
        work_parent = Path(args.work_dir).resolve() if args.work_dir else None
        if work_parent is not None:
            work_parent.mkdir(parents=True, exist_ok=True)
        estimated = _estimate_size(data_dir)
        try:
            free_bytes = shutil.disk_usage(work_parent or Path(tempfile.gettempdir())).free
        except OSError:
            free_bytes = -1
        if free_bytes >= 0 and estimated and free_bytes < estimated + 64 * 1024 * 1024:
            raise BackupError(
                f"临时目录可用空间不足：需要约 {_human_size(estimated)}，可用 {_human_size(free_bytes)}。"
                "请清理磁盘，或用 --work-dir 指定空间充足的位置。"
            )

        staging = Path(tempfile.mkdtemp(prefix="study-assistant-backup-", dir=str(work_parent) if work_parent else None))
        partial_zip = target.with_name(target.name + ".partial")
        try:
            if not args.quiet:
                print(f"正在生成 {DB_NAME} 一致性快照 ...")
            _snapshot_database(database, staging / DB_NAME)
            if not args.quiet:
                print("正在复制附件与索引目录 ...")
            file_count, dir_count, skipped = _copy_attachments(data_dir, staging)
            if skipped and not args.quiet:
                print(f"按规则跳过：{'、'.join(sorted(skipped))}")
            if not args.quiet:
                print(f"正在压缩（{_human_size(estimated)}，约 {file_count} 个文件）...")
            member_count = _zip_staging(staging, partial_zip)
            if not args.quiet:
                print("正在校验压缩包与数据库完整性 ...")
            stats = verify_archive(partial_zip, staging)
            os.replace(partial_zip, target)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            if partial_zip.exists():
                partial_zip.unlink(missing_ok=True)

        print("[OK] 备份成功")
        print(f"  来源: {data_dir}")
        print(f"  目标: {target}")
        print(f"  包大小: {_human_size(stats['archive_bytes'])}（{stats['members']} 个文件成员）")
        print(f"  内容: {DB_NAME}（SQLite 一致性快照）、其余数据目录内容（{file_count} 个文件、{dir_count} 个目录）")
        print(f"  校验: ZIP 可打开、成员齐全、{DB_NAME} integrity_check=ok、数据表 {stats['tables']} 张")
        print("  恢复: 先停止服务，再把压缩包内容解压到目标数据目录（相对路径保持包内结构）。")
        return 0
    except BackupError as exc:
        print(f"[错误] {exc}", file=sys.stderr)
        print("[错误] 备份未完成，未生成有效备份包。", file=sys.stderr)
        return EXIT_FAILURE
    except KeyboardInterrupt:
        print("[错误] 已取消备份。", file=sys.stderr)
        return EXIT_FAILURE
    except (OSError, sqlite3.Error, zipfile.BadZipFile) as exc:
        print(f"[错误] 备份失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        print("[错误] 备份未完成，未生成有效备份包。", file=sys.stderr)
        return EXIT_FAILURE


if __name__ == "__main__":
    sys.exit(main())
