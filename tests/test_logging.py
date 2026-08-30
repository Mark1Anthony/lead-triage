"""The app's own log lines have to survive into production.

Uvicorn only configures its own loggers. Without configure_logging() everything
this app logs below WARNING is dropped - which is what happened on the first
Render deployment: the token warning appeared, the line naming the database
backend did not. These tests are here so that does not come back quietly.
"""

import logging

import pytest
from fastapi.testclient import TestClient

import app as app_module
import db


@pytest.fixture(autouse=True)
def restore_logger():
    """configure_logging() mutates a module-level logger - undo that afterwards."""
    log = app_module.log
    handlers, level, propagate = log.handlers[:], log.level, log.propagate
    yield
    log.handlers, log.level, log.propagate = handlers, level, propagate


def test_the_startup_line_reaches_the_log(tmp_path, monkeypatch, caplog):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "SQLITE_PATH", tmp_path / "leads.db")
    monkeypatch.setenv("LEAD_TRIAGE_TOKEN", "test-token")

    with caplog.at_level(logging.INFO, logger="lead_triage"):
        with TestClient(app_module.app):
            pass

    assert "Database backend: sqlite" in caplog.text


def test_it_borrows_uvicorns_handler_when_there_is_one():
    uvicorn_logger = logging.getLogger("uvicorn.error")
    handler = logging.NullHandler()
    uvicorn_logger.addHandler(handler)
    try:
        app_module.configure_logging()
        # Same handler object, not a second one writing in a different format.
        assert handler in app_module.log.handlers
        assert app_module.log.propagate is False
    finally:
        uvicorn_logger.removeHandler(handler)


def test_it_falls_back_outside_uvicorn(monkeypatch):
    monkeypatch.setattr(logging.getLogger("uvicorn.error"), "handlers", [])
    app_module.configure_logging()
    assert app_module.log.level == logging.INFO
    assert app_module.log.propagate is True


def test_db_shares_the_app_logger_namespace():
    # A hyphen here would make db.py a sibling rather than a child, and it would
    # silently miss everything configure_logging() sets up.
    assert db.log.name.startswith(app_module.log.name + ".")


def test_it_finds_the_handler_on_the_parent_logger():
    # This is where uvicorn actually puts it: the "uvicorn" logger carries the
    # handler and "uvicorn.error" merely inherits it. Looking only at the child
    # is why the first attempt at this silently used the fallback format.
    uvicorn_logger = logging.getLogger("uvicorn")
    handler = logging.NullHandler()
    uvicorn_logger.addHandler(handler)
    try:
        app_module.configure_logging()
        assert handler in app_module.log.handlers
    finally:
        uvicorn_logger.removeHandler(handler)


def test_the_handler_list_is_not_shared():
    # Assigning uvicorn's list rather than a copy makes both loggers point at one
    # object; a later addHandler on either then mutates both.
    uvicorn_logger = logging.getLogger("uvicorn")
    handler = logging.NullHandler()
    uvicorn_logger.addHandler(handler)
    try:
        app_module.configure_logging()
        assert app_module.log.handlers is not uvicorn_logger.handlers

        app_module.log.addHandler(logging.NullHandler())
        assert len(uvicorn_logger.handlers) == 1
    finally:
        uvicorn_logger.removeHandler(handler)
