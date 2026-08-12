"""Tests for summarizer module."""

import pytest
from jdocmunch_mcp.parser.sections import Section
from jdocmunch_mcp.summarizer.batch_summarize import (
    heading_summary,
    title_fallback,
    summarize_sections,
    BatchSummarizer,
    _build_prompt,
    _parse_response,
    get_provider_name,
)


def make_section(title="Test Section", level=2, content="Some content here."):
    return Section(
        id=f"repo::doc.md::{title.lower().replace(' ', '-')}#{level}",
        repo="repo",
        doc_path="doc.md",
        title=title,
        content=content,
        level=level,
        parent_id="",
        children=[],
    )


class TestHeadingSummary:
    def test_returns_title(self):
        sec = make_section(title="Installation Guide")
        assert heading_summary(sec) == "Installation Guide"

    def test_truncates_long_title(self):
        long_title = "A" * 200
        sec = make_section(title=long_title)
        assert len(heading_summary(sec)) == 120


class TestTitleFallback:
    def test_level_1(self):
        sec = make_section(title="Overview", level=1)
        result = title_fallback(sec)
        assert "Section" in result or "Overview" in result

    def test_level_2(self):
        sec = make_section(title="Install", level=2)
        result = title_fallback(sec)
        assert "Subsection" in result or "Install" in result


class TestParseResponse:
    def test_basic_parse(self):
        text = "1. Explains installation.\n2. Covers configuration."
        result = _parse_response(text, 2)
        assert result[0] == "Explains installation."
        assert result[1] == "Covers configuration."

    def test_dotted_non_numbered_line_ignored(self):
        """Lines like 'e.g., something' or 'v1.2.3' should not corrupt output."""
        text = "e.g., some context\nv1.2.3 released\n1. Real summary here."
        result = _parse_response(text, 1)
        assert result[0] == "Real summary here."

    def test_out_of_range_ignored(self):
        text = "5. Out of range summary."
        result = _parse_response(text, 2)
        assert result == ["", ""]

    def test_partial_response(self):
        """Missing entries leave empty strings."""
        text = "1. First summary."
        result = _parse_response(text, 3)
        assert result[0] == "First summary."
        assert result[1] == ""
        assert result[2] == ""


class TestBuildPrompt:
    def test_contains_section_content(self):
        sec = Section(
            id="r::d::s#1", repo="r", doc_path="d.md", title="My Title",
            content="Some content here.", level=1, parent_id="", children=[],
        )
        prompt = _build_prompt([sec])
        assert "My Title" in prompt
        assert "Some content" in prompt
        assert "1." in prompt

    def test_numbered_correctly(self):
        secs = [
            Section(id=f"r::d::s{i}#1", repo="r", doc_path="d.md",
                    title=f"Title {i}", content="x", level=1, parent_id="", children=[])
            for i in range(3)
        ]
        prompt = _build_prompt(secs)
        assert "1." in prompt
        assert "2." in prompt
        assert "3." in prompt


class TestSummarizeSections:
    def test_no_ai(self):
        sections = [
            make_section("Overview", level=1),
            make_section("Installation", level=2),
            make_section("Usage", level=2),
        ]
        result = summarize_sections(sections, use_ai=False)
        for sec in result:
            assert sec.summary != ""

    def test_summaries_filled(self):
        sections = [make_section(f"Section {i}", level=2) for i in range(5)]
        result = summarize_sections(sections, use_ai=False)
        assert all(s.summary for s in result)

    def test_preserves_existing_summary(self):
        sec = make_section("Title")
        sec.summary = "Already summarized."
        result = summarize_sections([sec], use_ai=False)
        # summarize_sections seeds from heading for empty summaries
        # but shouldn't CLEAR an existing summary if heading_summary tier runs
        assert result[0].summary != ""


class TestGetProviderName:
    def test_explicit_anthropic(self, monkeypatch):
        monkeypatch.setenv("JDOCMUNCH_SUMMARIZER_PROVIDER", "anthropic")
        assert get_provider_name() == "anthropic"

    def test_explicit_gemini(self, monkeypatch):
        monkeypatch.setenv("JDOCMUNCH_SUMMARIZER_PROVIDER", "gemini")
        assert get_provider_name() == "gemini"

    def test_explicit_openai(self, monkeypatch):
        monkeypatch.setenv("JDOCMUNCH_SUMMARIZER_PROVIDER", "openai")
        assert get_provider_name() == "openai"

    def test_explicit_minimax(self, monkeypatch):
        monkeypatch.setenv("JDOCMUNCH_SUMMARIZER_PROVIDER", "minimax")
        assert get_provider_name() == "minimax"

    def test_explicit_glm(self, monkeypatch):
        monkeypatch.setenv("JDOCMUNCH_SUMMARIZER_PROVIDER", "glm")
        assert get_provider_name() == "glm"

    def test_explicit_none_disables(self, monkeypatch):
        monkeypatch.setenv("JDOCMUNCH_SUMMARIZER_PROVIDER", "none")
        assert get_provider_name() is None

    def test_auto_detect_anthropic(self, monkeypatch):
        monkeypatch.delenv("JDOCMUNCH_SUMMARIZER_PROVIDER", raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        monkeypatch.delenv("ZHIPUAI_API_KEY", raising=False)
        monkeypatch.setenv("JDOCMUNCH_ALLOW_PAID_SUMMARIES", "1")  # paid auto-detect is opt-in (v1.96.0)
        assert get_provider_name() == "anthropic"

    def test_auto_detect_gemini_fallback(self, monkeypatch):
        monkeypatch.delenv("JDOCMUNCH_SUMMARIZER_PROVIDER", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setenv("GOOGLE_API_KEY", "test-key")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        monkeypatch.delenv("ZHIPUAI_API_KEY", raising=False)
        monkeypatch.setenv("JDOCMUNCH_ALLOW_PAID_SUMMARIES", "1")  # paid auto-detect is opt-in (v1.96.0)
        assert get_provider_name() == "gemini"

    def test_auto_detect_minimax(self, monkeypatch):
        monkeypatch.delenv("JDOCMUNCH_SUMMARIZER_PROVIDER", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("MINIMAX_API_KEY", "test-key")
        monkeypatch.delenv("ZHIPUAI_API_KEY", raising=False)
        monkeypatch.setenv("JDOCMUNCH_ALLOW_PAID_SUMMARIES", "1")  # paid auto-detect is opt-in (v1.96.0)
        assert get_provider_name() == "minimax"

    def test_auto_detect_glm(self, monkeypatch):
        monkeypatch.delenv("JDOCMUNCH_SUMMARIZER_PROVIDER", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
        monkeypatch.setenv("ZHIPUAI_API_KEY", "test-key")
        monkeypatch.setenv("JDOCMUNCH_ALLOW_PAID_SUMMARIES", "1")  # paid auto-detect is opt-in (v1.96.0)
        assert get_provider_name() == "glm"

    def test_no_keys_returns_none(self, monkeypatch):
        for key in ["JDOCMUNCH_SUMMARIZER_PROVIDER", "ANTHROPIC_API_KEY",
                     "GOOGLE_API_KEY", "OPENAI_API_KEY", "MINIMAX_API_KEY",
                     "ZHIPUAI_API_KEY"]:
            monkeypatch.delenv(key, raising=False)
        assert get_provider_name() is None

    def test_explicit_overrides_auto_detect(self, monkeypatch):
        monkeypatch.setenv("JDOCMUNCH_SUMMARIZER_PROVIDER", "glm")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        assert get_provider_name() == "glm"

    def test_unknown_explicit_falls_through_to_auto(self, monkeypatch):
        monkeypatch.setenv("JDOCMUNCH_SUMMARIZER_PROVIDER", "unknown-provider")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("JDOCMUNCH_ALLOW_PAID_SUMMARIES", "1")  # paid auto-detect is opt-in (v1.96.0)
        assert get_provider_name() == "anthropic"

    def test_bare_paid_key_suppressed_without_opt_in(self, monkeypatch):
        """v1.96.0: a bare cloud key must NOT auto-enable a paid provider."""
        for key in ["JDOCMUNCH_SUMMARIZER_PROVIDER", "GOOGLE_API_KEY",
                    "OPENAI_API_KEY", "MINIMAX_API_KEY", "ZHIPUAI_API_KEY",
                    "JDOCMUNCH_ALLOW_PAID_SUMMARIES"]:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-would-bill")
        assert get_provider_name() is None

    def test_bare_paid_key_honored_with_env_opt_in(self, monkeypatch):
        """v1.96.0: opt-in restores legacy auto-select."""
        for key in ["JDOCMUNCH_SUMMARIZER_PROVIDER", "GOOGLE_API_KEY",
                    "OPENAI_API_KEY", "MINIMAX_API_KEY", "ZHIPUAI_API_KEY"]:
            monkeypatch.delenv(key, raising=False)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("JDOCMUNCH_ALLOW_PAID_SUMMARIES", "1")
        assert get_provider_name() == "anthropic"


class TestBackwardCompatAliases:
    def test_batch_summarizer_alias(self):
        from jdocmunch_mcp.summarizer.batch_summarize import (
            BatchSummarizer,
            _AnthropicSummarizer,
        )
        assert BatchSummarizer is _AnthropicSummarizer

    def test_gemini_batch_summarizer_alias(self):
        from jdocmunch_mcp.summarizer.batch_summarize import (
            GeminiBatchSummarizer,
            _GeminiSummarizer,
        )
        assert GeminiBatchSummarizer is _GeminiSummarizer


class TestOpenAICompatSummarizer:
    def test_call_api_returns_empty_string_for_none_content(self):
        from types import SimpleNamespace

        from jdocmunch_mcp.summarizer.batch_summarize import _OpenAICompatSummarizer

        summarizer = _OpenAICompatSummarizer.__new__(_OpenAICompatSummarizer)
        summarizer.model = "minimax-m2.7"
        summarizer._client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **_: SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(content=None))]
                    )
                )
            )
        )

        assert summarizer._call_api("prompt") == ""
