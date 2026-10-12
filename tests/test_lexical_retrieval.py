from __future__ import annotations

from typing import Any

from sqlalchemy.dialects import postgresql

from app.rag.lexical import lexical_document, lexical_query, lexical_query_terms
from app.rag.models import SEARCH_VECTOR_SQL, DocumentChunk, _search_text_default
from app.rag.retrieval import lexical_match, reciprocal_rank_fusion


def test_chinese_text_becomes_overlapping_bigrams() -> None:
    assert lexical_document("监督学习使用标签") == "监督 督学 学习 习使 使用 用标 标签"


def test_mixed_script_is_normalized_and_split_on_punctuation() -> None:
    terms = lexical_document("K-Means（聚类）ＡＢＣ Café_v2").split()
    assert terms == ["k", "means", "聚类", "abc", "café", "v2"]


def test_isolated_cjk_character_stays_searchable() -> None:
    assert lexical_document("A 类 B") == "a 类 b"


def test_chinese_question_matches_terms_from_the_document() -> None:
    document = set(
        lexical_document("监督学习（Supervised Learning）使用带标签的数据训练模型。").split()
    )
    query = lexical_query_terms("什么是监督学习？")
    assert {"监督", "督学", "学习"} <= set(query)
    assert "什么" not in query and "么是" not in query
    assert {"监督", "督学", "学习"} <= document


def test_english_question_drops_scaffolding_words() -> None:
    assert lexical_query_terms("What is the difference between bias and variance?") == [
        "bias",
        "variance",
    ]


def test_query_is_or_of_safe_terms_and_cannot_inject_operators() -> None:
    assert lexical_query("梯度 下降") == "梯度 | 下降"
    assert lexical_query("x' & y | !z:* (a) <-> b") is None
    query = lexical_query("rate' & loss | !gain")
    assert query == "rate | loss | gain"


def test_query_without_content_returns_none() -> None:
    assert lexical_query("是什么？") is None
    assert lexical_query("   ") is None
    assert lexical_match("？？") is None


def test_query_terms_are_bounded_and_unique() -> None:
    terms = lexical_query_terms(" ".join(f"term{i} term{i}" for i in range(200)))
    assert len(terms) == 48
    assert len(set(terms)) == 48


def test_lexical_match_uses_indexed_expression() -> None:
    match = lexical_match("监督学习")
    assert match is not None
    condition, rank = match
    dialect = postgresql.dialect()
    sql = str(condition.compile(dialect=dialect))
    assert "to_tsvector('simple'::regconfig, review_agent.document_chunks.search_text)" in sql
    assert "@@ to_tsquery('simple'::regconfig," in sql
    assert "ts_rank_cd(to_tsvector('simple'::regconfig" in str(rank.compile(dialect=dialect))
    assert SEARCH_VECTOR_SQL == "to_tsvector('simple'::regconfig, search_text)"


def test_model_declares_gin_index_on_search_terms() -> None:
    indexes = {index.name: index for index in DocumentChunk.__table__.indexes}
    index = indexes["ix_document_chunks_search"]
    assert index.dialect_options["postgresql"]["using"] == "gin"


def test_insert_default_tokenizes_content() -> None:
    class Context:
        def get_current_parameters(self) -> dict[str, Any]:
            return {"content": "主成分分析 PCA"}

    assert _search_text_default(Context()) == "主成 成分 分分 分析 pca"


def test_reciprocal_rank_fusion_rewards_agreement_and_keeps_lexical_only_hits() -> None:
    scores = reciprocal_rank_fusion((["a", "b"], ["c", "a"]))
    ranked = sorted(scores, key=lambda key: scores[key], reverse=True)
    assert ranked[0] == "a"
    assert set(ranked) == {"a", "b", "c"}
    assert scores["b"] == 1 / 62
    assert scores["c"] == 1 / 61
