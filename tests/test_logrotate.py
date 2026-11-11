"""Log rotation helpers: RotatingFileHandler logger, access log, copy-truncate."""

from __future__ import annotations

import logging
import logging.handlers

import logrotate


def test_rotating_logger_uses_rotating_handler_and_skips_stderr(tmp_path):
    lg = logrotate.rotating_logger("t-access", tmp_path, max_bytes=5_000_000, backups=3)
    handlers = [h for h in lg.handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert len(handlers) == 1
    assert handlers[0].maxBytes == 5_000_000 and handlers[0].backupCount == 3
    assert lg.propagate is False
    assert not any(type(h) is logging.StreamHandler for h in lg.handlers)
    lg.info("hello")
    assert "hello" in (tmp_path / "t-access.log").read_text()
    # asking again does not stack handlers
    assert len(logrotate.rotating_logger("t-access", tmp_path).handlers) == 1


def test_rotating_handler_rolls_at_cap(tmp_path):
    lg = logrotate.rotating_logger("t-small", tmp_path, max_bytes=300, backups=2)
    for i in range(50):
        lg.info("x" * 40 + str(i))
    assert (tmp_path / "t-small.log.1").exists()
    assert not (tmp_path / "t-small.log.3").exists()


def test_access_log_middleware_logs_line(tmp_path):
    lg = logrotate.rotating_logger("t-mw", tmp_path)

    def app(environ, start_response):
        start_response("404 Not Found", [("Content-Length", "2")])
        return [b"no"]

    wrapped = logrotate.AccessLog(app, lg)
    seen = {}
    body = wrapped(
        {"REQUEST_METHOD": "GET", "PATH_INFO": "/x", "QUERY_STRING": "a=1", "REMOTE_ADDR": "1.2.3.4"},
        lambda status, headers, exc=None: seen.update(status=status),
    )
    assert body == [b"no"] and seen["status"].startswith("404")
    line = (tmp_path / "t-mw.log").read_text()
    assert '1.2.3.4 "GET /x?a=1" 404 2B' in line


def test_rotate_logs_copy_truncates_only_big_files(tmp_path):
    big = tmp_path / "monitor.log"
    big.write_bytes(b"a" * 600)
    small = tmp_path / "dashboard.log"
    small.write_bytes(b"b" * 10)
    err = tmp_path / "dashboard.err"
    err.write_bytes(b"c" * 700)
    rotated = logrotate.rotate_logs(tmp_path, max_bytes=500, backups=2)
    assert {p.name for p in rotated} == {"monitor.log", "dashboard.err"}
    assert big.stat().st_size == 0 and (tmp_path / "monitor.log.1").read_bytes() == b"a" * 600
    assert small.read_bytes() == b"b" * 10
    # a second rotation shifts the older copy to .2 and honours the backup count
    big.write_bytes(b"d" * 600)
    logrotate.rotate_logs(tmp_path, max_bytes=500, backups=2)
    assert (tmp_path / "monitor.log.2").read_bytes() == b"a" * 600
    assert (tmp_path / "monitor.log.1").read_bytes() == b"d" * 600
    big.write_bytes(b"e" * 600)
    logrotate.rotate_logs(tmp_path, max_bytes=500, backups=2)
    assert not (tmp_path / "monitor.log.3").exists()


def test_rotate_logs_missing_dir_is_fine(tmp_path):
    assert logrotate.rotate_logs(tmp_path / "nope") == []
