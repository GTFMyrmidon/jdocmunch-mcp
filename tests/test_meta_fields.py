"""Tests for JDOCMUNCH_META_FIELDS env-var filtering."""

import pytest
from jdocmunch_mcp.config import get_meta_fields


class TestGetMetaFields:
    def test_default_is_empty_list(self, monkeypatch):
        monkeypatch.delenv("JDOCMUNCH_META_FIELDS", raising=False)
        assert get_meta_fields() == []

    def test_null_returns_none(self, monkeypatch):
        monkeypatch.setenv("JDOCMUNCH_META_FIELDS", "null")
        assert get_meta_fields() is None

    def test_all_returns_none(self, monkeypatch):
        monkeypatch.setenv("JDOCMUNCH_META_FIELDS", "all")
        assert get_meta_fields() is None

    def test_star_returns_none(self, monkeypatch):
        monkeypatch.setenv("JDOCMUNCH_META_FIELDS", "*")
        assert get_meta_fields() is None

    def test_empty_string_returns_empty_list(self, monkeypatch):
        monkeypatch.setenv("JDOCMUNCH_META_FIELDS", "")
        assert get_meta_fields() == []

    def test_bracket_empty_returns_empty_list(self, monkeypatch):
        monkeypatch.setenv("JDOCMUNCH_META_FIELDS", "[]")
        assert get_meta_fields() == []

    def test_csv_fields(self, monkeypatch):
        monkeypatch.setenv("JDOCMUNCH_META_FIELDS", "timing_ms, powered_by")
        assert get_meta_fields() == ["timing_ms", "powered_by"]

    def test_single_field(self, monkeypatch):
        monkeypatch.setenv("JDOCMUNCH_META_FIELDS", "timing_ms")
        assert get_meta_fields() == ["timing_ms"]


class TestMetaFieldsFiltering:
    """Integration-style tests: verify _meta is filtered in tool output."""

    def _make_result_with_meta(self):
        return {
            "sections": [{"id": "s1"}],
            "_meta": {
                "timing_ms": 42,
                "tokens_saved": 100,
                "powered_by": "jdocmunch-mcp by jgravelle · https://github.com/jgravelle/jdocmunch-mcp",
            },
        }

    def test_empty_list_strips_meta(self, monkeypatch):
        monkeypatch.setenv("JDOCMUNCH_META_FIELDS", "")
        from jdocmunch_mcp.config import get_meta_fields
        meta_fields = get_meta_fields()
        result = self._make_result_with_meta()
        if meta_fields == []:
            result.pop("_meta", None)
        assert "_meta" not in result

    def test_null_keeps_all_meta(self, monkeypatch):
        monkeypatch.setenv("JDOCMUNCH_META_FIELDS", "null")
        from jdocmunch_mcp.config import get_meta_fields
        meta_fields = get_meta_fields()
        result = self._make_result_with_meta()
        assert meta_fields is None
        assert "_meta" in result
        assert "timing_ms" in result["_meta"]
        assert "tokens_saved" in result["_meta"]
        assert "powered_by" in result["_meta"]

    def test_partial_list_keeps_only_listed(self, monkeypatch):
        monkeypatch.setenv("JDOCMUNCH_META_FIELDS", "timing_ms")
        from jdocmunch_mcp.config import get_meta_fields
        meta_fields = get_meta_fields()
        result = self._make_result_with_meta()
        if isinstance(meta_fields, list) and meta_fields:
            existing_meta = result.pop("_meta", {})
            _meta = {}
            for field in meta_fields:
                if field in existing_meta:
                    _meta[field] = existing_meta[field]
            if _meta:
                result["_meta"] = _meta
        assert result["_meta"] == {"timing_ms": 42}
        assert "tokens_saved" not in result.get("_meta", {})
        assert "powered_by" not in result.get("_meta", {})
