"""Archive batching preserves history, ordering, corruption checks and isolation."""
import copy
import hashlib
import sqlite3

import pytest

from worldline import state_storage as storage
from worldline.engine import encode


@pytest.fixture
def archive():
    db = sqlite3.connect(":memory:")
    storage.ensure_schema(db)
    cache = {None: set()}
    values = [{"date": "1900-01-02", "data": [1, True, -0.0]},
              {"date": "1900-01-01", "data": [1.0, False, 0.0]}]
    for owner, item, value in (("b", "second", values[0]), ("a", "first", values[1]),
                               ("a", "duplicate", values[0])):
        storage._store(db, value, "report", owner, item, cache)
    db.commit()
    yield db
    db.close()


def test_history_single_query_preserves_order_filters_and_distinct_result_objects(archive):
    statements = []
    archive.set_trace_callback(statements.append)
    rows = storage.history(archive, kind="report")
    archive.set_trace_callback(None)
    assert len(statements) == 1
    assert [(row["day"], row["owner"], row["item"]) for row in rows] == [
        ("1900-01-01", "a", "first"), ("1900-01-02", "a", "duplicate"), ("1900-01-02", "b", "second")]
    assert rows[1]["digest"] == rows[2]["digest"]
    assert rows[1]["value"] is not rows[2]["value"]
    rows[1]["value"]["data"].append(99)
    assert rows[2]["value"]["data"] == [1, True, -0.0]
    assert storage.history(archive, kind="report", owner="a", item="duplicate")[0]["value"]["data"] == [1, True, -0.0]
    assert storage.history(archive, kind="unknown") == []


def test_export_two_queries_preserves_exact_types_signed_zero_and_checksums(archive):
    statements = []
    archive.set_trace_callback(statements.append)
    exported = storage.export_archive(archive)
    archive.set_trace_callback(None)
    assert len(statements) == 2
    assert list(exported["objects"]) == sorted(exported["objects"])
    assert len(exported["objects"]) == 2 and len(exported["catalog"]) == 3
    for digest, value in exported["objects"].items():
        assert hashlib.sha256(encode(value).encode("utf-8")).hexdigest() == digest
    storage.validate_archive(exported)
    prior = copy.deepcopy(exported)
    next(iter(exported["objects"].values()))["data"].append(99)
    assert encode(storage.export_archive(archive)) == encode(prior)


def test_history_missing_catalog_blob_raises_instead_of_omitting_row(archive):
    archive.execute("DROP TRIGGER archive_no_delete")
    archive.execute("DELETE FROM state_archive WHERE digest=(SELECT digest FROM archive_catalog LIMIT 1)")
    with pytest.raises(ValueError, match="Missing"):
        storage.history(archive, kind="report")


@pytest.mark.parametrize("query", [storage.history, storage.export_archive])
@pytest.mark.parametrize("field,value", [("codec", "unknown"), ("raw_bytes", 1), ("payload", b"invalid-zlib")])
def test_batched_queries_keep_archive_validation(archive, query, field, value):
    archive.execute("DROP TRIGGER archive_no_update")
    archive.execute("UPDATE state_archive SET " + field + "=?", (value,))
    import zlib
    with pytest.raises((ValueError, zlib.error)):
        query(archive, kind="report") if query is storage.history else query(archive)


def test_export_empty_archive_keeps_original_none_contract():
    db = sqlite3.connect(":memory:")
    try:
        storage.ensure_schema(db)
        assert storage.export_archive(db) is None
    finally:
        db.close()


def test_query_local_raw_cache_does_not_hide_later_external_corruption(archive):
    storage.history(archive, kind="report")
    archive.execute("DROP TRIGGER archive_no_update")
    archive.execute("UPDATE state_archive SET raw_bytes=raw_bytes+1")
    with pytest.raises(ValueError, match="checksum"):
        storage.history(archive, kind="report")
