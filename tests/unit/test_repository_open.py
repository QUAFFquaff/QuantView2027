from __future__ import annotations

import os

import duckdb
import pytest

from quantview.data.repository import Repository


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing violations only")
def test_transient_windows_sharing_failure_reopens_once_without_replaying_body(tmp_path, monkeypatch):
    repository = Repository(tmp_path / "test.duckdb")
    original = duckdb.connect
    calls, body, waits = [], [], []

    def open_database(*args, **kwargs):
        calls.append(True)
        if len(calls) <= 2:
            raise duckdb.IOException("Cannot open file: 另一个程序正在使用此文件，进程无法访问。")
        return original(*args, **kwargs)

    monkeypatch.setattr("quantview.data.repository.duckdb.connect", open_database)
    monkeypatch.setattr("quantview.data.repository.time.sleep", waits.append)
    with repository.connect() as connection:
        body.append(True)
        assert connection.execute("select 42").fetchone() == (42,)
    assert len(calls) == 3 and len(body) == 1 and sum(waits) < 1


@pytest.mark.skipif(os.name != "nt", reason="Windows sharing violations only")
@pytest.mark.parametrize("message,expected_attempts", [
    ("Access is denied", 1), ("Database file is corrupt", 1),
    ("The process cannot access the file because it is being used by another process", 5),
])
def test_permanent_errors_are_not_hidden_and_sharing_retry_is_bounded(tmp_path, monkeypatch, message, expected_attempts):
    repository = Repository(tmp_path / "test.duckdb")
    calls, waits = [], []

    def fail(*args, **kwargs):
        calls.append(True)
        raise duckdb.IOException(message)

    monkeypatch.setattr("quantview.data.repository.duckdb.connect", fail)
    monkeypatch.setattr("quantview.data.repository.time.sleep", waits.append)
    with pytest.raises(duckdb.IOException, match=message):
        with repository.connect():
            pytest.fail("A failed open must not execute a transaction")
    assert len(calls) == expected_attempts and sum(waits) < 1
