"""The database layer's dialect handling.

These run without a database - they check the translation between the two
backends. The full API suite runs against both for real: SQLite by default,
Postgres when TEST_DATABASE_URL is set (CI does that).
"""

import pytest

import db


class TestBackendDetection:
    def test_no_url_means_sqlite(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert db.is_postgres() is False
        assert db.backend() == "sqlite"

    def test_empty_url_means_sqlite(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "")
        assert db.is_postgres() is False

    def test_postgres_url_is_detected(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@host:5432/leads")
        assert db.is_postgres() is True
        assert db.backend() == "postgres"

    def test_the_older_postgres_scheme_counts_too(self, monkeypatch):
        # Some providers still hand out postgres:// rather than postgresql://
        monkeypatch.setenv("DATABASE_URL", "postgres://user:pw@host:5432/leads")
        assert db.is_postgres() is True

    def test_a_sqlite_url_is_not_postgres(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "sqlite:///data/leads.db")
        assert db.is_postgres() is False


class TestPlaceholders:
    def test_sqlite_keeps_question_marks(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert db.sql("SELECT * FROM leads WHERE id = ?") == "SELECT * FROM leads WHERE id = ?"

    def test_postgres_gets_percent_s(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://host/leads")
        assert db.sql("SELECT * FROM leads WHERE id = ?") == "SELECT * FROM leads WHERE id = %s"

    def test_every_placeholder_is_translated(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://host/leads")
        translated = db.sql("INSERT INTO leads (a, b, c) VALUES (?, ?, ?)")
        assert translated.count("%s") == 3
        assert "?" not in translated


class TestSchema:
    def test_sqlite_uses_autoincrement(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert "AUTOINCREMENT" in db.schema()
        assert "IDENTITY" not in db.schema()

    def test_postgres_uses_an_identity_column(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://host/leads")
        assert "IDENTITY" in db.schema()
        assert "AUTOINCREMENT" not in db.schema()

    def test_both_declare_the_same_columns(self, monkeypatch):
        columns = [
            "received_at", "name", "company", "email", "source", "message",
            "priority", "category", "next_action", "summary", "reasoning",
            "mode", "status",
        ]
        for column in columns:
            assert column in db.SCHEMA_SQLITE
            assert column in db.SCHEMA_POSTGRES

    def test_both_are_idempotent(self):
        # Startup runs this on every boot, against an existing table.
        assert "IF NOT EXISTS" in db.SCHEMA_SQLITE
        assert "IF NOT EXISTS" in db.SCHEMA_POSTGRES


class TestStartupWait:
    """init() retries while the database is coming up, but not on real errors."""

    @staticmethod
    def _postgres(monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgresql://leads:leads@db:5432/leads")

    def test_a_refused_connection_is_an_operational_error(self, monkeypatch):
        # The retry only works if this is the exception psycopg actually raises.
        # Nothing listens on port 1, so this fails immediately.
        import psycopg

        monkeypatch.setenv(
            "DATABASE_URL", "postgresql://x:x@127.0.0.1:1/x?connect_timeout=2"
        )
        with pytest.raises(psycopg.OperationalError):
            db.init(attempts=1)

    def test_it_retries_until_the_database_answers(self, monkeypatch):
        import psycopg

        self._postgres(monkeypatch)
        calls = []

        def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise psycopg.OperationalError("connection refused")

        monkeypatch.setattr(db, "_create_schema", flaky)
        monkeypatch.setattr(db.time, "sleep", lambda _: None)

        db.init(attempts=5, delay=0)
        assert len(calls) == 3

    def test_it_gives_up_eventually(self, monkeypatch):
        import psycopg

        self._postgres(monkeypatch)
        calls = []

        def always_down():
            calls.append(1)
            raise psycopg.OperationalError("connection refused")

        monkeypatch.setattr(db, "_create_schema", always_down)
        monkeypatch.setattr(db.time, "sleep", lambda _: None)

        with pytest.raises(psycopg.OperationalError):
            db.init(attempts=4, delay=0)
        assert len(calls) == 4

    def test_a_broken_statement_is_not_retried(self, monkeypatch):
        # Waiting ten seconds to report a typo helps nobody.
        import psycopg

        self._postgres(monkeypatch)
        calls = []

        def broken():
            calls.append(1)
            raise psycopg.ProgrammingError('syntax error at or near "CRAETE"')

        monkeypatch.setattr(db, "_create_schema", broken)
        monkeypatch.setattr(db.time, "sleep", lambda _: None)

        with pytest.raises(psycopg.ProgrammingError):
            db.init(attempts=5, delay=0)
        assert len(calls) == 1

    def test_sqlite_does_not_wait(self, monkeypatch):
        monkeypatch.delenv("DATABASE_URL", raising=False)
        slept = []
        monkeypatch.setattr(db.time, "sleep", lambda s: slept.append(s))
        db.init()
        assert slept == []
