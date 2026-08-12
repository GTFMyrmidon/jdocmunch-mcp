"""Tests for v1.18.0: structured OpenAPI retrieval."""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from jdocmunch_mcp.parser.openapi_structured import parse_openapi_structured, _refs_in, _ref_name, _op_key
from jdocmunch_mcp.parser.sections import Section
from jdocmunch_mcp.storage import DocStore
from jdocmunch_mcp.tools.index_local import index_local


PETSTORE_YAML = textwrap.dedent("""
openapi: 3.0.0
info:
  title: Petstore
  version: "1.0.0"
  description: Tiny API for tests.
paths:
  /pets:
    get:
      operationId: listPets
      summary: List pets
      tags: [pets]
      parameters:
        - name: limit
          in: query
          schema: { type: integer }
      responses:
        "200":
          description: ok
          content:
            application/json:
              schema:
                type: array
                items: { $ref: "#/components/schemas/Pet" }
    post:
      operationId: createPet
      summary: Create pet
      tags: [pets]
      requestBody:
        required: true
        content:
          application/json:
            schema: { $ref: "#/components/schemas/Pet" }
      responses:
        "201": { description: created }
  /pets/{id}:
    get:
      operationId: getPet
      summary: Get pet
      tags: [pets]
      parameters:
        - name: id
          in: path
          required: true
          schema: { type: integer }
      responses:
        "200":
          description: ok
          content:
            application/json:
              schema: { $ref: "#/components/schemas/Pet" }
    delete:
      operationId: deletePet
      summary: Delete pet
      tags: [pets]
      deprecated: true
      parameters:
        - name: id
          in: path
          required: true
          schema: { type: integer }
      responses:
        "204": { description: no content }
  /health:
    get:
      summary: Health check
      responses: { "200": { description: ok } }
components:
  schemas:
    Pet:
      type: object
      required: [id, name]
      properties:
        id: { type: integer }
        name: { type: string }
        tag: { $ref: "#/components/schemas/Tag" }
    Tag:
      type: object
      required: [name]
      properties:
        name: { type: string }
        slug: { type: string }
""").strip()


@pytest.fixture()
def petstore_repo(tmp_path):
    """Index PETSTORE_YAML and return (storage_path, repo_id)."""
    repo_dir = tmp_path / "specs"
    repo_dir.mkdir()
    (repo_dir / "petstore.yaml").write_text(PETSTORE_YAML, encoding="utf-8")
    index_local(
        path=str(repo_dir),
        name="petstore",
        use_ai_summaries=False,
        use_embeddings=False,
        storage_path=str(tmp_path),
        incremental=False,
    )
    return str(tmp_path), "petstore"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_ref_name_basic(self):
        assert _ref_name("#/components/schemas/User") == "User"

    def test_ref_name_empty(self):
        assert _ref_name("") == ""

    def test_op_key_prefers_operationId(self):
        assert _op_key("GET", "/pets/{id}", {"operationId": "GetPet"}) == "getpet"

    def test_op_key_falls_back_to_method_path(self):
        assert _op_key("GET", "/pets/{id}", {}) == "get-pets-id"

    def test_refs_in_walks_nested(self):
        schema = {
            "type": "object",
            "properties": {
                "a": {"$ref": "#/components/schemas/Foo"},
                "b": {"items": {"$ref": "#/components/schemas/Bar"}},
            },
        }
        out = _refs_in(schema)
        assert "Foo" in out
        assert "Bar" in out


# ---------------------------------------------------------------------------
# parse_openapi_structured
# ---------------------------------------------------------------------------

class TestStructuredParser:
    def test_returns_empty_when_not_openapi(self):
        # Plain markdown — no openapi/swagger key.
        assert parse_openapi_structured("# Hello\n\nbody", "x.yaml", "r") == []

    def test_returns_empty_on_unparseable(self):
        assert parse_openapi_structured("{{{ not yaml or json }}}", "x.yaml", "r") == []

    def test_emits_root_operations_schemas(self):
        secs = parse_openapi_structured(PETSTORE_YAML, "petstore.yaml", "local/r")
        titles = [s.title for s in secs]
        assert "Petstore" in titles
        assert "Operations" in titles
        assert "Schemas" in titles

    def test_op_metadata_populated(self):
        secs = parse_openapi_structured(PETSTORE_YAML, "petstore.yaml", "local/r")
        ops = [s for s in secs if "openapi_op" in s.metadata]
        assert len(ops) == 5  # listPets, createPet, getPet, deletePet, health
        list_pets = next(s for s in ops if s.metadata["openapi_op"]["operationId"] == "listPets")
        op = list_pets.metadata["openapi_op"]
        assert op["method"] == "GET"
        assert op["path"] == "/pets"
        assert op["tags"] == ["pets"]
        assert any(p["name"] == "limit" for p in op["parameters"])
        assert "Pet" in op["responses"]["200"]["refs"]

    def test_deprecated_flag_carried(self):
        secs = parse_openapi_structured(PETSTORE_YAML, "petstore.yaml", "local/r")
        delete = next(
            s for s in secs
            if s.metadata.get("openapi_op", {}).get("operationId") == "deletePet"
        )
        assert delete.metadata["openapi_op"]["deprecated"] is True

    def test_schema_metadata_includes_used_by_operations(self):
        secs = parse_openapi_structured(PETSTORE_YAML, "petstore.yaml", "local/r")
        pet = next(
            s for s in secs
            if s.metadata.get("openapi_schema", {}).get("name") == "Pet"
        )
        meta = pet.metadata["openapi_schema"]
        assert "id" in meta["properties"]
        assert "name" in meta["required"]
        # Pet is referenced by listPets (response), createPet (request body), getPet (response).
        assert set(meta["used_by_operations"]) >= {"createpet", "getpet", "listpets"}

    def test_untagged_operation_lands_under_operations_untagged(self):
        secs = parse_openapi_structured(PETSTORE_YAML, "petstore.yaml", "local/r")
        # /health has no tags — should appear with operationId None.
        health = next(
            s for s in secs
            if s.metadata.get("openapi_op", {}).get("path") == "/health"
        )
        assert health.metadata["openapi_op"]["tags"] == []

    def test_schema_refs_recovered(self):
        secs = parse_openapi_structured(PETSTORE_YAML, "petstore.yaml", "local/r")
        pet = next(
            s for s in secs
            if s.metadata.get("openapi_schema", {}).get("name") == "Pet"
        )
        # Pet has a property `tag` with $ref to Tag.
        assert "Tag" in pet.metadata["openapi_schema"]["refs"]


# ---------------------------------------------------------------------------
# Storage round-trip — Section.metadata persists through save+load
# ---------------------------------------------------------------------------

class TestStorageRoundtrip:
    def test_metadata_persists(self, petstore_repo):
        storage_path, repo = petstore_repo
        store = DocStore(base_path=storage_path)
        idx = store.load_index("local", repo)
        op_secs = [s for s in idx.sections if "openapi_op" in (s.get("metadata") or {})]
        schema_secs = [s for s in idx.sections if "openapi_schema" in (s.get("metadata") or {})]
        assert len(op_secs) == 5
        assert len(schema_secs) == 2  # Pet, Tag

    def test_metadata_omitted_when_empty(self):
        s = Section(
            id="r::d::s#1", repo="r", doc_path="d.md", title="T", content="",
            level=1, parent_id="", children=[],
        )
        d = s.to_dict()
        assert "metadata" not in d


# ---------------------------------------------------------------------------
# find_endpoint
# ---------------------------------------------------------------------------

class TestFindEndpoint:
    def test_filter_by_method(self, petstore_repo):
        from jdocmunch_mcp.tools.openapi_tools import find_endpoint
        storage_path, repo = petstore_repo
        out = find_endpoint(repo=repo, method="GET", storage_path=storage_path)
        methods = {r["method"] for r in out["results"]}
        assert methods == {"GET"}
        assert len(out["results"]) >= 3  # listPets, getPet, health

    def test_filter_by_path_glob(self, petstore_repo):
        from jdocmunch_mcp.tools.openapi_tools import find_endpoint
        storage_path, repo = petstore_repo
        out = find_endpoint(repo=repo, path="/pets/*", storage_path=storage_path)
        for r in out["results"]:
            assert r["path"].startswith("/pets/")

    def test_filter_by_tag(self, petstore_repo):
        from jdocmunch_mcp.tools.openapi_tools import find_endpoint
        storage_path, repo = petstore_repo
        out = find_endpoint(repo=repo, tag="pets", storage_path=storage_path)
        # health has no tag → excluded.
        assert all(r["path"] != "/health" for r in out["results"])
        assert len(out["results"]) == 4

    def test_combined_filters(self, petstore_repo):
        from jdocmunch_mcp.tools.openapi_tools import find_endpoint
        storage_path, repo = petstore_repo
        out = find_endpoint(repo=repo, method="get", path="/pets", storage_path=storage_path)
        assert len(out["results"]) == 1
        assert out["results"][0]["operationId"] == "listPets"

    def test_unknown_repo(self, tmp_path):
        from jdocmunch_mcp.tools.openapi_tools import find_endpoint
        out = find_endpoint(repo="nope/missing", storage_path=str(tmp_path))
        assert "error" in out


# ---------------------------------------------------------------------------
# list_endpoints_by_tag
# ---------------------------------------------------------------------------

class TestListByTag:
    def test_pets_tag(self, petstore_repo):
        from jdocmunch_mcp.tools.openapi_tools import list_endpoints_by_tag
        storage_path, repo = petstore_repo
        out = list_endpoints_by_tag(repo=repo, tag="pets", storage_path=storage_path)
        assert len(out["results"]) == 4

    def test_unknown_tag_empty(self, petstore_repo):
        from jdocmunch_mcp.tools.openapi_tools import list_endpoints_by_tag
        storage_path, repo = petstore_repo
        out = list_endpoints_by_tag(repo=repo, tag="nope", storage_path=storage_path)
        assert out["results"] == []


# ---------------------------------------------------------------------------
# find_operations_using_schema
# ---------------------------------------------------------------------------

class TestFindOpsUsingSchema:
    def test_pet_used_by_three_ops(self, petstore_repo):
        from jdocmunch_mcp.tools.openapi_tools import find_operations_using_schema
        storage_path, repo = petstore_repo
        out = find_operations_using_schema(repo=repo, schema_name="Pet", storage_path=storage_path)
        op_ids = {r["operationId"] for r in out["results"]}
        assert {"listPets", "createPet", "getPet"} <= op_ids

    def test_unknown_schema_returns_empty(self, petstore_repo):
        from jdocmunch_mcp.tools.openapi_tools import find_operations_using_schema
        storage_path, repo = petstore_repo
        out = find_operations_using_schema(repo=repo, schema_name="DoesNotExist", storage_path=storage_path)
        assert out["results"] == []


# ---------------------------------------------------------------------------
# get_schema_graph
# ---------------------------------------------------------------------------

class TestSchemaGraph:
    def test_pet_walks_to_tag(self, petstore_repo):
        from jdocmunch_mcp.tools.openapi_tools import get_schema_graph
        storage_path, repo = petstore_repo
        out = get_schema_graph(repo=repo, schema_name="Pet", storage_path=storage_path)
        assert "Pet" in out["nodes"]
        assert "Tag" in out["nodes"]
        assert ["Pet", "Tag"] in out["edges"]

    def test_unknown_schema_returns_error(self, petstore_repo):
        from jdocmunch_mcp.tools.openapi_tools import get_schema_graph
        storage_path, repo = petstore_repo
        out = get_schema_graph(repo=repo, schema_name="Nope", storage_path=storage_path)
        assert "error" in out

    def test_max_depth_bounds_walk(self, petstore_repo):
        from jdocmunch_mcp.tools.openapi_tools import get_schema_graph
        storage_path, repo = petstore_repo
        out = get_schema_graph(repo=repo, schema_name="Pet", max_depth=0, storage_path=storage_path)
        # depth=0 → root only, no traversal.
        assert "Pet" in out["nodes"]
        assert "Tag" not in out["nodes"]


# ---------------------------------------------------------------------------
# Server registration
# ---------------------------------------------------------------------------

class TestServerRegistration:
    def test_new_tools_registered(self):
        import asyncio
        from jdocmunch_mcp import server as srv
        tools = asyncio.run(srv.list_tools())
        names = {t.name for t in tools}
        for n in ("find_endpoint", "list_endpoints_by_tag", "find_operations_using_schema", "get_schema_graph"):
            assert n in names
