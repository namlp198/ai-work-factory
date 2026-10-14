"""`awf start --log-file --supervise`: what Task Scheduler / services rely on."""

from __future__ import annotations

import argparse
import sys

import pytest

from awf import cli


def test_rotating_log_stream(tmp_path):
    log = tmp_path / "logs" / "awf.log"
    stream = cli._RotatingStream(log, max_bytes=200, backups=2)
    for i in range(60):
        stream.write(f"line {i:03d} " + "x" * 20 + "\n")
    stream.flush()
    files = sorted(p.name for p in log.parent.iterdir())
    assert files == ["awf.log", "awf.log.1", "awf.log.2"]  # never more than `backups` old files
    assert all(p.stat().st_size < 400 for p in log.parent.iterdir())
    assert "line 059" in log.read_text(encoding="utf-8")


def test_supervisor_restarts_after_crash_and_stops_on_config_error(monkeypatch, tmp_path):
    calls = []

    def fake_start(args):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")  # unexpected crash -> restart
        if len(calls) == 2:
            return 0  # clean exit -> restart as well (a service should keep running)
        raise SystemExit("bad registration token")  # configuration error -> give up

    monkeypatch.setattr(cli, "cmd_start", fake_start)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    old_out, old_err = sys.stdout, sys.stderr
    try:
        code = cli.cmd_start_entry(argparse.Namespace(role=None, supervise=True, log_file=str(tmp_path / "s.log")))
    finally:
        sys.stdout, sys.stderr = old_out, old_err
    assert len(calls) == 3 and code == 1
    text = (tmp_path / "s.log").read_text(encoding="utf-8")
    assert "crashed (RuntimeError: boom)" in text and "bad registration token" in text


def test_without_supervise_runs_once(monkeypatch):
    monkeypatch.setattr(cli, "cmd_start", lambda args: 7)
    assert cli.cmd_start_entry(argparse.Namespace(role=None, supervise=False, log_file=None)) == 7
