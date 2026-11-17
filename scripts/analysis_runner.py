#!/usr/bin/env python3
"""Run one analysis script inside the project directory.

Started by analysis.py as ``python -I``. The script is data the agent wrote,
not a file from sources/. Network APIs and paths outside the project are refused.
This is a process limit for mistakes and untrusted inputs, not a kernel sandbox.
"""

from __future__ import annotations

import importlib
import io
import os
import stat
import sys
from pathlib import Path


DENIED_MODULES = frozenset(
    {
        "asyncio",
        "ctypes",
        "ftplib",
        "http",
        "imaplib",
        "multiprocessing",
        "nntplib",
        "poplib",
        "smtplib",
        "socket",
        "ssl",
        "subprocess",
        "telnetlib",
        "urllib",
        "webbrowser",
        "xmlrpc",
    }
)
NETWORK_MODULES = frozenset(
    {
        "asyncio",
        "ftplib",
        "http",
        "imaplib",
        "nntplib",
        "poplib",
        "smtplib",
        "socket",
        "ssl",
        "telnetlib",
        "urllib",
        "webbrowser",
        "xmlrpc",
    }
)


def configure_stdio() -> None:
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            continue


def fail(message: str, code: int) -> None:
    sys.stderr.write(message.rstrip() + "\n")
    raise SystemExit(code)


class _DenyImports:
    def find_spec(self, fullname, path, target=None):  # noqa: ANN001
        root = fullname.split(".", 1)[0]
        if root not in DENIED_MODULES:
            return None
        if root in sys.modules and root == "socket":
            return None
        if root in NETWORK_MODULES:
            raise PermissionError("сеть в анализе запрещена")
        raise PermissionError("этот модуль в анализе запрещён")


def install_jail(project: Path) -> None:
    project = project.resolve()
    read_roots = []
    for raw in (
        sys.prefix,
        sys.base_prefix,
        sys.exec_prefix,
        str(Path(sys.executable).resolve().parent),
        *sys.path,
    ):
        if not raw:
            continue
        try:
            root = Path(raw).resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if root not in read_roots:
            read_roots.append(root)

    real_lstat = os.lstat
    real_readlink = os.readlink

    def normalized(path: Path, depth: int = 0) -> Path:
        """Absolute path without calling the patched os.stat. Symlinks are unfolded once."""
        if depth > 8:
            raise PermissionError(f"выход за папку проекта запрещён: {path}")
        try:
            absolute = Path(os.path.normpath(os.path.abspath(os.fspath(path))))
        except (OSError, RuntimeError, ValueError) as exc:
            raise PermissionError(f"выход за папку проекта запрещён: {path}") from exc
        current = Path(absolute.anchor)
        for part in absolute.parts[1:]:
            current = current / part
            try:
                info = real_lstat(current)
            except OSError:
                continue
            if not stat.S_ISLNK(info.st_mode):
                continue
            try:
                target = Path(real_readlink(current))
            except OSError as exc:
                raise PermissionError(f"выход за папку проекта запрещён: {current}") from exc
            if not target.is_absolute():
                target = current.parent / target
            current = normalized(target, depth + 1)
        return current

    def allowed(path: Path, *, write: bool) -> Path:
        resolved = normalized(path)
        if resolved == project or project in resolved.parents:
            return resolved
        if write:
            raise PermissionError(f"выход за папку проекта запрещён: {resolved}")
        for root in read_roots:
            if resolved == root or root in resolved.parents:
                return resolved
        raise PermissionError(f"выход за папку проекта запрещён: {resolved}")

    def as_path(file) -> Path | None:
        if isinstance(file, int):
            return None
        path = Path(file)
        if not path.is_absolute():
            path = Path.cwd() / path
        return path

    real_open = builtins_open
    real_io_open = io.open
    real_os_open = os.open

    def guarded_open(file, mode="r", *args, **kwargs):
        path = as_path(file)
        if path is not None:
            writing = any(flag in str(mode) for flag in ("w", "a", "x", "+"))
            allowed(path, write=writing)
        return real_open(file, mode, *args, **kwargs)

    def guarded_io_open(file, mode="r", *args, **kwargs):
        path = as_path(file)
        if path is not None:
            writing = any(flag in str(mode) for flag in ("w", "a", "x", "+"))
            allowed(path, write=writing)
        return real_io_open(file, mode, *args, **kwargs)

    write_flags = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT

    def guarded_os_open(path, flags, mode=0o777, *, dir_fd=None):
        if not isinstance(path, int):
            file_path = as_path(path)
            if file_path is not None:
                allowed(file_path, write=bool(flags & write_flags))
        if dir_fd is None:
            return real_os_open(path, flags, mode)
        return real_os_open(path, flags, mode, dir_fd=dir_fd)

    def guard_write(func, *, paths: int = 1):
        def wrapped(*args, **kwargs):
            for item in args[:paths]:
                file_path = as_path(item)
                if file_path is not None:
                    allowed(file_path, write=True)
            return func(*args, **kwargs)

        return wrapped

    def guard_read(func):
        def wrapped(path, *args, **kwargs):
            file_path = as_path(path)
            if file_path is not None:
                allowed(file_path, write=False)
            return func(path, *args, **kwargs)

        return wrapped

    def blocked_process(*_args, **_kwargs):
        raise PermissionError("запуск процессов в анализе запрещён")

    def blocked_network(*_args, **_kwargs):
        raise PermissionError("сеть в анализе запрещена")

    import builtins

    builtins.open = guarded_open
    io.open = guarded_io_open
    os.open = guarded_os_open
    for name in ("remove", "unlink", "rmdir", "mkdir", "makedirs"):
        if hasattr(os, name):
            setattr(os, name, guard_write(getattr(os, name)))
    for name in ("rename", "replace"):
        if hasattr(os, name):
            setattr(os, name, guard_write(getattr(os, name), paths=2))

    def blocked_link(*_args, **_kwargs):
        raise PermissionError("ссылки в анализе запрещены")

    for name in ("link", "symlink"):
        if hasattr(os, name):
            setattr(os, name, blocked_link)
    import pathlib

    real_path_open = pathlib.Path.open

    def guarded_path_open(self, mode="r", *args, **kwargs):
        writing = any(flag in str(mode) for flag in ("w", "a", "x", "+"))
        allowed(Path(self), write=writing)
        return real_path_open(self, mode, *args, **kwargs)

    pathlib.Path.open = guarded_path_open  # type: ignore[method-assign]
    for name in ("listdir", "scandir", "stat", "lstat"):
        if hasattr(os, name):
            setattr(os, name, guard_read(getattr(os, name)))
    real_chdir = os.chdir

    def guarded_chdir(path):
        file_path = as_path(path)
        if file_path is not None:
            allowed(file_path, write=False)
        return real_chdir(path)

    os.chdir = guarded_chdir
    for name in ("system", "popen", "execv", "execve", "execvp", "execvpe", "spawnv", "spawnve", "startfile"):
        if hasattr(os, name):
            setattr(os, name, blocked_process)

    import socket

    socket.socket = blocked_network  # type: ignore[misc]
    socket.create_connection = blocked_network
    socket.create_server = blocked_network
    socket.getaddrinfo = blocked_network
    if hasattr(socket, "socketpair"):
        socket.socketpair = blocked_network

    real_reload = importlib.reload

    def guarded_reload(module):
        name = str(getattr(module, "__name__", "")).split(".", 1)[0]
        if name in DENIED_MODULES:
            if name in NETWORK_MODULES or name == "socket":
                raise PermissionError("сеть в анализе запрещена")
            raise PermissionError("этот модуль в анализе запрещён")
        return real_reload(module)

    importlib.reload = guarded_reload
    sys.meta_path.insert(0, _DenyImports())


# Captured before install_jail replaces builtins.open.
import builtins

builtins_open = builtins.open


def main(argv: list[str]) -> int:
    configure_stdio()
    if len(argv) != 7:
        fail("Внутренняя ошибка запуска анализа.", 1)
    project = Path(argv[1])
    script_path = Path(argv[2])
    input_path = Path(argv[3])
    result_path = Path(argv[4])
    chart_path = Path(argv[5])
    analysis_id = argv[6]
    try:
        project_root = project.resolve()
        script_resolved = script_path.resolve()
        if project_root != script_resolved and project_root not in script_resolved.parents:
            fail("выход за папку проекта запрещён: скрипт", 2)
    except (OSError, RuntimeError, ValueError) as exc:
        fail(f"выход за папку проекта запрещён: {exc}", 2)
    os.environ["STRAT_ANALYSIS_PROJECT"] = str(project_root)
    os.environ["STRAT_ANALYSIS_INPUT"] = str(input_path)
    os.environ["STRAT_ANALYSIS_RESULT"] = str(result_path)
    os.environ["STRAT_ANALYSIS_CHART"] = str(chart_path)
    os.environ["STRAT_ANALYSIS_ID"] = analysis_id
    os.chdir(script_resolved.parent)
    try:
        install_jail(project_root)
        source = builtins.open(script_resolved, encoding="utf-8").read()
        compiled = compile(source, str(script_resolved), "exec")
        exec(compiled, {"__name__": "__main__", "__file__": str(script_resolved)})
    except PermissionError as exc:
        fail(str(exc) or "выход за папку проекта запрещён", 2)
    except SystemExit as exc:
        code = exc.code
        if code in (None, 0):
            return 0 if result_path.is_file() else fail("Скрипт не записал STRAT_ANALYSIS_RESULT.", 3)
        fail(f"Скрипт завершился с кодом {code}.", 1)
    except Exception as exc:  # noqa: BLE001 — show the script error, do not hide the cause
        fail(f"Скрипт анализа упал: {exc.__class__.__name__}: {exc}", 1)
    if not result_path.is_file():
        fail("Скрипт не записал STRAT_ANALYSIS_RESULT.", 3)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
