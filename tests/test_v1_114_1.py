# -*- coding: utf-8 -*-
"""v1.114.1 — BM25 tokenizer no longer discards non-ASCII text (#91).

Through v1.114.0 the split regex was ``[^a-z0-9]+``, so every non-ASCII
character acted as a separator: Korean/Japanese/Chinese content produced
zero tokens (BM25 contributed nothing; hybrid search silently degraded to
semantic-only) and accented Latin was mangled (``café`` → ``caf``).

The fix splits on Unicode word boundaries and expands CJK runs (which have
no whitespace word boundaries) into overlapping character bigrams — applied
identically at index and query time, so bigram overlap is the match signal.
``search_titles``'s private ASCII-only tokenizer got the same treatment via
the shared ``word_tokens`` helper.
"""

from jdocmunch_mcp.retrieval.bm25 import compute_corpus_stats, score_section
from jdocmunch_mcp.retrieval.tokenize import tokenize, word_tokens
from jdocmunch_mcp.tools.search_titles import _score_title, _tokenize


class TestCJKTokenization:
    def test_korean_produces_bigrams(self):
        # Issue #91 repro: was [].
        toks = tokenize("초과근무 승인 규칙")
        assert toks == ["초과", "과근", "근무", "승인", "규칙"]

    def test_mixed_camelcase_and_korean(self):
        # Issue #91 repro: was ['overtime', 'service'] only.
        toks = tokenize("OvertimeService 초과근무 계산")
        assert "overtime" in toks and "service" in toks
        assert "초과" in toks and "근무" in toks and "계산" in toks

    def test_accented_latin_survives_intact(self):
        # Issue #91 repro: was ['caf', 'na', 've'].
        assert tokenize("café naïve") == ["café", "naïve"]

    def test_japanese_bigrams(self):
        toks = tokenize("日本語のドキュメント")
        assert "日本" in toks and "本語" in toks
        assert all(len(t) == 2 for t in toks)

    def test_single_cjk_char_kept(self):
        # A lone CJK char is a real word — never dropped by the min-length
        # filter that applies to non-CJK tokens.
        assert tokenize("車") == ["車"]

    def test_mixed_script_run_splits(self):
        # No separator between the scripts — the CJK pad still splits them.
        toks = tokenize("초과근무OvertimeService")
        assert "overtime" in toks and "초과" in toks

    def test_ascii_behavior_unchanged(self):
        assert tokenize("Hello World") == ["hello", "world"]
        assert tokenize("DocStore embed_query") == ["doc", "store", "embed", "query"]
        assert "the" not in tokenize("the quick fox")


class TestBM25EndToEnd:
    def test_korean_query_matches_korean_section(self):
        sections = [
            {"id": "r::a.md::overtime#1", "title": "초과근무 승인",
             "summary": "", "content": "초과근무 승인 규칙과 계산 방법."},
            {"id": "r::b.md::unrelated#1", "title": "Deployment",
             "summary": "", "content": "How to deploy the service."},
        ]
        stats = compute_corpus_stats(sections)
        hit = score_section(sections[0], "초과근무", stats=stats)
        miss = score_section(sections[1], "초과근무", stats=stats)
        assert hit > 0.0, "Korean query must produce a positive BM25 score (#91)"
        assert miss == 0.0
        assert hit > miss


class TestSearchTitlesParity:
    def test_title_tokenizer_is_unicode_aware(self):
        assert _tokenize("초과근무 승인") == ["초과", "과근", "근무", "승인"]
        assert _tokenize("Café Guide") == ["café", "guide"]

    def test_korean_title_scores_by_token_overlap(self):
        title = "초과근무 승인 규칙"
        query = "초과근무"
        score = _score_title(
            title.lower(), _tokenize(title), query.lower(), set(_tokenize(query))
        )
        assert score > 0.0

    def test_word_tokens_no_stopword_or_length_filter(self):
        # The minimal tokenizer keeps what the BM25 one filters.
        assert word_tokens("The API") == ["the", "api"]
