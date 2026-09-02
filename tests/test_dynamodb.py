"""The DynamoDB backend's own behaviour.

The endpoint suite in test_api.py already runs against DynamoDB when
TEST_DYNAMODB is set, so this file only covers what is specific to the store
and invisible from an HTTP response: the id counter, the reserved attribute
name, and the fact that neither leaks into a listing.

moto answers botocore locally, so none of this needs an AWS account.
"""

import pytest

import db

pytest.importorskip("moto")

TABLE = "leads-unit"


@pytest.fixture
def dynamo(monkeypatch):
    from moto import mock_aws

    monkeypatch.setenv("AWS_DEFAULT_REGION", "eu-central-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DYNAMODB_TABLE", TABLE)

    mock = mock_aws()
    mock.start()
    import boto3

    boto3.client("dynamodb").create_table(
        TableName=TABLE,
        AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "N"}],
        KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
        BillingMode="PAY_PER_REQUEST",
    )
    yield
    mock.stop()


def record(name: str = "Sarah", **over) -> dict:
    base = {field: "x" for field in db.FIELDS}
    base.update(name=name, company="Nord Capital", priority="hot")
    base.update(over)
    return base


class TestSelection:
    def test_the_table_variable_decides(self, dynamo):
        assert db.backend() == "dynamodb"

    def test_it_wins_over_a_database_url(self, dynamo, monkeypatch):
        # Lambda gets both when a stack is migrated; the table is the newer
        # intent and has to take precedence, or the function would try to open
        # a Postgres connection it has no network path to.
        monkeypatch.setenv("DATABASE_URL", "postgresql://x:y@host/db")
        assert db.backend() == "dynamodb"


class TestIds:
    def test_they_start_at_one(self, dynamo):
        assert db.insert_lead(record()) == 1

    def test_they_increment(self, dynamo):
        ids = [db.insert_lead(record(f"lead {i}")) for i in range(4)]
        assert ids == [1, 2, 3, 4]

    def test_the_counter_is_not_a_lead(self, dynamo):
        # It lives in the same table under id 0 and must never be rendered.
        db.insert_lead(record())
        rows = db.list_leads()
        assert len(rows) == 1
        assert all(r["id"] != 0 for r in rows)
        assert db.count_leads() == 1


class TestReads:
    def test_newest_first(self, dynamo):
        for name in ("first", "second", "third"):
            db.insert_lead(record(name))
        assert [r["name"] for r in db.list_leads()] == ["third", "second", "first"]

    def test_a_row_has_every_field_the_templates_use(self, dynamo):
        db.insert_lead(record())
        row = db.list_leads()[0]
        for field in db.FIELDS:
            assert field in row
        assert row["status"] == "new"
        # An int, not a Decimal: it goes into a URL and into JSON.
        assert isinstance(row["id"], int)

    def test_empty_is_empty(self, dynamo):
        assert db.list_leads() == []
        assert db.count_leads() == 0


class TestWrites:
    def test_status_can_be_changed(self, dynamo):
        # "status" is a reserved word in DynamoDB expressions; without an alias
        # this raises ValidationException rather than updating anything.
        lead_id = db.insert_lead(record())
        db.set_status(lead_id, "won")
        assert db.list_leads()[0]["status"] == "won"

    def test_delete_removes_only_that_lead(self, dynamo):
        keep = db.insert_lead(record("keep"))
        drop = db.insert_lead(record("drop"))
        db.delete_lead(drop)
        remaining = db.list_leads()
        assert [r["id"] for r in remaining] == [keep]

    def test_deleting_twice_is_not_an_error(self, dynamo):
        # DynamoDB deletes are idempotent, and a double click should not 500.
        lead_id = db.insert_lead(record())
        db.delete_lead(lead_id)
        db.delete_lead(lead_id)
        assert db.list_leads() == []


class TestInit:
    def test_it_does_not_create_the_table(self, dynamo, monkeypatch):
        # Terraform owns the table. A Lambda able to create one would need
        # dynamodb:CreateTable, which is not a permission a request handler
        # should hold.
        from botocore.exceptions import ClientError

        monkeypatch.setenv("DYNAMODB_TABLE", "never-created")
        with pytest.raises(ClientError, match="ResourceNotFound"):
            db.init()

    def test_it_accepts_a_table_that_exists(self, dynamo):
        db.init()
