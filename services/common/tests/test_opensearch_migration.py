"""
Unit tests for OpensearchVectorStore schema detection and legacy-index migration.

An in-memory fake OpenSearch client models indexes (mapping + documents) so the
tests verify end state rather than individual client calls.
"""

import copy
from unittest.mock import Mock

import pytest
from opensearchpy.exceptions import NotFoundError

from common.opensearch import (
    MIGRATION_INDEX_SUFFIX,
    SCHEMA_VERSION,
    OpensearchNotReadyError,
    OpensearchVectorStore,
)

INDEX = "rag_test"
TEMP = f"{INDEX}{MIGRATION_INDEX_SUFFIX}"
DIM = 4


def _flatten(source):
    src = copy.deepcopy(source)
    meta = src.pop("metadata", None)
    if isinstance(meta, dict):
        src.update(meta)
    return src


class FakeIndices:
    def __init__(self, store):
        self._s = store

    def get_mapping(self, index):
        if index not in self._s.indexes:
            raise NotFoundError(404, "index_not_found_exception", {})
        return {index: {"mappings": copy.deepcopy(self._s.indexes[index]["mappings"])}}

    def exists(self, index):
        return index in self._s.indexes

    def create(self, index, body):
        if index in self._s.indexes:
            raise RuntimeError(f"resource_already_exists_exception: {index}")
        self._s.indexes[index] = {"mappings": copy.deepcopy(body["mappings"]), "docs": {}}

    def delete(self, index):
        self._s.indexes.pop(index)

    def refresh(self, index):
        pass


class FakeClient:
    def __init__(self):
        self.indexes = {}
        self.indices = FakeIndices(self)
        self.reindex_calls = []
        self.fail_on_reindex_dest = None

    def reindex(self, body, **params):
        src, dest = body["source"]["index"], body["dest"]["index"]
        self.reindex_calls.append((src, dest, "script" in body))
        if dest == self.fail_on_reindex_dest:
            raise RuntimeError("simulated crash during reindex")
        for _id, doc in self.indexes[src]["docs"].items():
            self.indexes[dest]["docs"][_id] = _flatten(doc) if "script" in body else copy.deepcopy(doc)
        return {"failures": []}

    def count(self, index):
        return {"count": len(self.indexes[index]["docs"])}

    def delete_by_query(self, index, body, params):
        return {"deleted": 0, "total": 0, "failures": []}


def _legacy_mapping():
    return {
        "properties": {
            "chunk_id": {"type": "long"},
            "embedding": {"type": "knn_vector", "dimension": DIM},
            "text": {"type": "text"},
            "metadata": {"properties": {"doc_id": {"type": "keyword"}, "language": {"type": "keyword"}}},
        }
    }


def _legacy_doc(i):
    return {
        "chunk_id": i,
        "embedding": [0.1] * DIM,
        "text": f"chunk {i}",
        "metadata": {"doc_id": f"doc-{i}", "language": "en", "page_number": i},
    }


def _make_store(client):
    inst = OpensearchVectorStore.__new__(OpensearchVectorStore)
    inst.index_name = INDEX
    inst.num_shards = 1
    inst.client = client
    return inst


def _add_legacy_index(client, name=INDEX, n=3):
    client.indexes[name] = {"mappings": _legacy_mapping(), "docs": {str(i): _legacy_doc(i) for i in range(n)}}


def _assert_migrated(client, n=3):
    assert TEMP not in client.indexes
    main = client.indexes[INDEX]
    assert main["mappings"]["_meta"]["schema_version"] == SCHEMA_VERSION
    assert main["mappings"]["properties"]["embedding"]["dimension"] == DIM
    assert len(main["docs"]) == n
    for i in range(n):
        doc = main["docs"][str(i)]
        assert "metadata" not in doc
        assert doc["doc_id"] == f"doc-{i}"
        assert doc["language"] == "en"
        assert doc["page_number"] == i
        assert doc["embedding"] == [0.1] * DIM


@pytest.mark.unit
class TestSchemaDetection:
    def test_missing_index_returns_none(self):
        assert _make_store(FakeClient())._get_schema_version(INDEX) is None

    def test_legacy_index_detected_as_v1(self):
        client = FakeClient()
        _add_legacy_index(client)
        assert _make_store(client)._get_schema_version(INDEX) == 1

    def test_current_index_detected_from_meta(self):
        client = FakeClient()
        store = _make_store(client)
        store._create_index(INDEX, DIM)
        assert store._get_schema_version(INDEX) == SCHEMA_VERSION

    def test_index_body_keeps_source_enabled(self):
        mappings = _make_store(FakeClient())._index_body(DIM)["mappings"]
        assert "_source" not in mappings
        assert mappings["_meta"] == {"schema_version": SCHEMA_VERSION}
        assert "metadata" not in mappings["properties"]


@pytest.mark.unit
class TestMigrateLegacyIndex:
    def test_absent_index_is_noop(self):
        client = FakeClient()
        assert _make_store(client).migrate_legacy_index() == "absent"
        assert client.indexes == {}

    def test_current_index_is_noop(self):
        client = FakeClient()
        store = _make_store(client)
        store._create_index(INDEX, DIM)
        assert store.migrate_legacy_index() == "current"
        assert client.reindex_calls == []

    def test_legacy_index_migrated_in_place(self):
        client = FakeClient()
        _add_legacy_index(client)
        assert _make_store(client).migrate_legacy_index() == "migrated"
        _assert_migrated(client)
        assert client.reindex_calls == [(INDEX, TEMP, True), (TEMP, INDEX, False)]

    def test_count_mismatch_aborts_without_deleting_legacy_index(self):
        client = FakeClient()
        _add_legacy_index(client)
        original_reindex = client.reindex

        def lossy_reindex(body, **params):
            result = original_reindex(body, **params)
            client.indexes[body["dest"]["index"]]["docs"].pop("0")
            return result

        client.reindex = lossy_reindex
        with pytest.raises(RuntimeError, match="count mismatch"):
            _make_store(client).migrate_legacy_index()

        assert client.indexes[INDEX]["mappings"] == _legacy_mapping()
        assert len(client.indexes[INDEX]["docs"]) == 3

    def test_resume_after_crash_before_legacy_delete(self):
        """Crash during the first copy: legacy index intact, partial temp index present."""
        client = FakeClient()
        _add_legacy_index(client)
        client.fail_on_reindex_dest = TEMP
        with pytest.raises(RuntimeError):
            _make_store(client).migrate_legacy_index()
        assert TEMP in client.indexes

        client.fail_on_reindex_dest = None
        assert _make_store(client).migrate_legacy_index() == "migrated"
        _assert_migrated(client)

    def test_resume_after_crash_with_main_index_missing(self):
        """Crash after the legacy index was deleted: only the temp index holds the data."""
        client = FakeClient()
        _add_legacy_index(client)
        client.fail_on_reindex_dest = INDEX
        with pytest.raises(RuntimeError):
            _make_store(client).migrate_legacy_index()
        client.indexes.pop(INDEX)  # simulate crash before the main index was recreated

        client.fail_on_reindex_dest = None
        assert _make_store(client).migrate_legacy_index() == "migrated"
        _assert_migrated(client)

    def test_resume_after_crash_during_final_copy(self):
        """Crash while copying back: main index is current but incomplete, temp still present."""
        client = FakeClient()
        _add_legacy_index(client)
        client.fail_on_reindex_dest = INDEX
        with pytest.raises(RuntimeError):
            _make_store(client).migrate_legacy_index()
        assert client.indexes[INDEX]["mappings"]["_meta"]["schema_version"] == SCHEMA_VERSION

        client.fail_on_reindex_dest = None
        assert _make_store(client).migrate_legacy_index() == "migrated"
        _assert_migrated(client)


@pytest.mark.unit
class TestCallersHandleLegacyIndex:
    def test_setup_index_creates_new_index_when_absent(self):
        client = FakeClient()
        _make_store(client)._setup_index(DIM)
        assert client.indexes[INDEX]["mappings"]["_meta"]["schema_version"] == SCHEMA_VERSION

    def test_setup_index_migrates_legacy_index(self):
        client = FakeClient()
        _add_legacy_index(client)
        _make_store(client)._setup_index(DIM)
        _assert_migrated(client)

    def test_search_on_legacy_index_raises_not_ready(self):
        client = FakeClient()
        _add_legacy_index(client)
        with pytest.raises(OpensearchNotReadyError, match="migration pending"):
            _make_store(client).search("query", vector=[0.1] * DIM)
        assert client.indexes[INDEX]["mappings"] == _legacy_mapping()  # read path never migrates

    def test_search_on_missing_index_raises_not_ready(self):
        with pytest.raises(OpensearchNotReadyError, match="Index is empty"):
            _make_store(FakeClient()).search("query", vector=[0.1] * DIM)

    def test_delete_migrates_legacy_index_first(self):
        client = FakeClient()
        _add_legacy_index(client)
        client.delete_by_query = Mock(return_value={"deleted": 1})
        _make_store(client).remove_docs_from_index(["doc-1"])
        _assert_migrated(client)
        assert client.delete_by_query.call_args.kwargs["body"]["query"] == {"terms": {"doc_id": ["doc-1"]}}

    def test_delete_on_missing_index_returns_zero(self):
        assert _make_store(FakeClient()).remove_docs_from_index(["doc-1"]) == 0
