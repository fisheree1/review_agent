import json
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest

from app.learning.overview import (
    OverviewChunk,
    merge_overview_answers,
    overview_batches,
    select_overview_chunks,
)


def document(name: str, pages: int, *, dense_first: int = 1) -> list[OverviewChunk]:
    doc = uuid5(NAMESPACE_URL, name)
    result = []
    for page in range(1, pages + 1):
        for part in range(dense_first if page == 1 else 1):
            result.append(
                OverviewChunk(uuid5(doc, f"{page}-{part}"), doc, 1, page, len(result) + 1)
            )
    return result


CASES = json.loads((Path(__file__).resolve().parents[1] / "evals/overview-v1.json").read_text())[
    "cases"
]


@pytest.mark.parametrize(
    "pages,dense_first",
    [(case["pages"], case["dense_first"]) for case in CASES],
    ids=[case["id"] for case in CASES],
)
def test_page_strata_cover_opening_middle_and_ending_without_dense_page_starvation(
    pages, dense_first
):
    chunks = document("long-pdf", pages, dense_first=dense_first)
    selected = select_overview_chunks(chunks)
    units = {chunk.unit for chunk in selected}
    assert 1 in units and pages in units
    assert len(units) == min(pages, 64)
    count = len(chunks)
    old = {
        chunks[min(count - 1, (2 * i + 1) * count // (2 * min(15, count)))].unit
        for i in range(min(15, count))
    }
    assert len(units) > len(old)
    assert len(selected) == min(len(chunks), 64)
    assert any(pages // 3 <= unit <= 2 * pages // 3 for unit in units)
    assert select_overview_chunks(list(reversed(chunks))) == selected
    batches = overview_batches(selected)
    assert all(1 <= len(batch) <= 8 for batch in batches) and len(batches) <= 8
    assert len({chunk.id for batch in batches for chunk in batch}) == len(selected)
    assert min(c.unit for c in batches[0]) == 1
    assert max(c.unit for c in batches[0]) == pages


def test_multi_document_quota_favors_long_document_but_first_batch_keeps_every_document():
    long = document("long", 300)
    short = document("short", 4)
    selected = select_overview_chunks(long + short)
    assert sum(c.document_id == long[0].document_id for c in selected) > 50
    assert {c.document_id for c in overview_batches(selected)[0]} == {
        long[0].document_id,
        short[0].document_id,
    }
    assert len(selected) == 64


def test_all_chunks_fit_and_merge_preserves_more_than_eight_distinct_points():
    chunks = document("short", 4, dense_first=9)
    assert {c.id for c in select_overview_chunks(chunks)} == {c.id for c in chunks}

    def answer(start):
        return {
            "insufficient_evidence": False,
            "claims": [
                {"text": f"Point {i}", "citations": [{"source_id": str(i), "unit": i}]}
                for i in range(start, start + 8)
            ],
            "explanation": f"Explain {start}",
        }

    merged = merge_overview_answers(answer(1), answer(9))
    assert len(merged["claims"]) == 16
    assert "Explain 1" in merged["explanation"] and "Explain 9" in merged["explanation"]
    assert merge_overview_answers(merged, answer(9)) == merged
    assert merge_overview_answers(merged, {"insufficient_evidence": True, "claims": []}) == merged


def test_empty_overview_has_no_batches():
    assert overview_batches(select_overview_chunks([])) == []


def test_repeated_fact_merges_citations_without_merging_other_documents():
    def point(source, doc):
        return {
            "claims": [
                {
                    "text": "Repeated fact",
                    "citations": [
                        {
                            "source_id": source,
                            "document_id": doc,
                            "unit": 1,
                            "quote": "supported excerpt",
                        }
                    ],
                }
            ]
        }

    merged = merge_overview_answers(point("a", "doc"), point("b", "doc"))
    assert len(merged["claims"]) == 1
    assert len(merged["claims"][0]["citations"]) == 2
    assert len(merge_overview_answers(merged, point("c", "other"))["claims"]) == 2


def test_merged_sections_keep_their_own_teaching_and_pages_across_batch_replay():
    def answer(page):
        return {
            "insufficient_evidence": False,
            "claims": [
                {
                    "title": f"Topic {page}",
                    "text": f"Fact {page}",
                    "explanation": f"Teaching {page}",
                    "citations": [{"source_id": str(page), "unit": page}],
                }
            ],
        }

    merged = merge_overview_answers(answer(14), answer(3))
    assert [
        (claim["title"], claim["explanation"], claim["citations"][0]["unit"])
        for claim in merged["claims"]
    ] == [("Topic 3", "Teaching 3", 3), ("Topic 14", "Teaching 14", 14)]
    assert merge_overview_answers(merged, answer(3)) == merged
