"""Log hygiene: rotating app logs, an access log that stays off stderr, and
copy-truncate rotation for the files launchd owns.

Stdlib only. Three pieces:

* `rotating_logger()` — a logger writing to `<log_dir>/<name>.log` through
  `logging.handlers.RotatingFileHandler` (default 5 MB x 3 backups), never to stderr.
* `AccessLog` — tiny WSGI middleware that writes one access line per request to
  such a logger, replacing the lines that used to fill `logs/dashboard.err`.
* `rotate_logs()` — copy-truncate rotation of `logs/*.log` / `*.err`. launchd opens
  `StandardOutPath` / `StandardErrorPath` itself and keeps the descriptor, so a
  handler inside the process cannot rotate those; copying then truncating in place
  works while launchd keeps appending.
"""

from __future__ import annotations

import logging
import logging.handlers
import shutil
import time
from pathlib import Path
from typing import Callable, Iterable

DEFAULT_MAX_BYTES = 5_000_000
DEFAULT_BACKUPS = 3


def rotating_logger(
    name: str,
    log_dir: str | Path,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backups: int = DEFAULT_BACKUPS,
    level: int = logging.INFO,
    fmt: str = "%(asctime)s %(message)s",
) -> logging.Logger:
    """Logger that writes only to `<log_dir>/<name>.log` (rotating) and does not propagate."""
    log_dir = Path(log_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False  # keep it off the root/stderr handlers
    path = str((log_dir / f"{name}.log").resolve())
    for h in logger.handlers:
        if isinstance(h, logging.handlers.RotatingFileHandler) and h.baseFilename == path:
            return logger
    handler = logging.handlers.RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backups)
    handler.setFormatter(logging.Formatter(fmt))
    logger.addHandler(handler)
    return logger


def route_to_logger(names: Iterable[str], logger: logging.Logger) -> None:
    """Send third-party loggers (waitress, werkzeug) to `logger`'s handlers, not stderr."""
    for name in names:
        lg = logging.getLogger(name)
        lg.handlers = list(logger.handlers)
        lg.propagate = False


class AccessLog:
    """WSGI middleware: `host "GET /path?q" 200 1234B 12ms` per request."""

    def __init__(self, app: Callable, logger: logging.Logger) -> None:
        self.app = app
        self.logger = logger

    def __call__(self, environ, start_response):
        t0 = time.monotonic()
        status_box: list[str] = []

        def _start(status, headers, exc_info=None):
            status_box.append(status.split(" ", 1)[0])
            self._size = next((h[1] for h in headers if h[0].lower() == "content-length"), "-")
            return start_response(status, headers, exc_info)

        self._size = "-"
        try:
            return self.app(environ, _start)
        finally:
            qs = environ.get("QUERY_STRING")
            path = environ.get("PATH_INFO", "") + (f"?{qs}" if qs else "")
            self.logger.info(
                '%s "%s %s" %s %sB %dms',
                environ.get("REMOTE_ADDR", "-"),
                environ.get("REQUEST_METHOD", "-"),
                path,
                status_box[0] if status_box else "-",
                self._size,
                (time.monotonic() - t0) * 1000,
            )


def rotate_logs(
    log_dir: str | Path,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backups: int = DEFAULT_BACKUPS,
    patterns: tuple[str, ...] = ("*.log", "*.err"),
) -> list[Path]:
    """Copy-truncate every matching file over `max_bytes`; keep `backups` old copies
    (`name.log.1` newest). Files already managed by a RotatingFileHandler are
    rotated by it long before they reach the cap, so they are skipped by size.
    Returns the files rotated."""
    log_dir = Path(log_dir)
    rotated: list[Path] = []
    if not log_dir.is_dir():
        return rotated
    for pattern in patterns:
        for f in sorted(log_dir.glob(pattern)):
            try:
                if not f.is_file() or f.stat().st_size <= max_bytes:
                    continue
                for i in range(backups - 1, 0, -1):
                    src = f.with_name(f"{f.name}.{i}")
                    if src.exists():
                        src.replace(f.with_name(f"{f.name}.{i + 1}"))
                if backups > 0:
                    shutil.copyfile(f, f.with_name(f"{f.name}.1"))
                with open(f, "r+b") as fh:  # truncate in place: the writer keeps its fd
                    fh.truncate(0)
                rotated.append(f)
            except OSError:
                continue
    return rotated
