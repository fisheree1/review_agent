from scripts.evaluate_ca6000_rag import capabilities, score_answer


def test_capability_probe_does_not_call_single_document_api_multi_document() -> None:
    openapi = {
        "paths": {
            "/api/v1/documents/{document_id}/questions": {
                "post": {
                    "requestBody": {
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/AskRequest"}
                            }
                        }
                    }
                }
            }
        },
        "components": {"schemas": {"AskRequest": {"properties": {"question": {"type": "string"}}}}},
    }

    assert capabilities(openapi) == {
        "multi_document_scope": "NOT_IMPLEMENTED",
        "continuous_conversation": "NOT_IMPLEMENTED",
        "answer_feedback": "NOT_IMPLEMENTED",
    }


def test_answer_requires_exact_source_quote_and_expected_locator() -> None:
    case = {
        "expected": "answer",
        "locator": {"kind": "page", "position": 3},
        "required_term_groups": [["random"], ["choice as ch"]],
    }
    source = {
        3: {
            "content": "from random import choice as ch",
            "citation_locator": {"kind": "page", "position": 3, "title": None, "path": []},
        }
    }
    question = {
        "status": "answered",
        "answer": {
            "insufficient_evidence": False,
            "claims": [
                {
                    "text": "Use from random import choice as ch.",
                    "citations": [
                        {
                            "source_id": "example-source",
                            "unit": 3,
                            "quote": "from random import choice as ch",
                            "locator": source[3]["citation_locator"],
                        }
                    ],
                }
            ],
        },
    }

    assert score_answer(case, question, source)["passed"] is True
    question["answer"]["claims"][0]["citations"][0]["quote"] = "invented text"
    result = score_answer(case, question, source)
    assert result["passed"] is False
    assert "citation_quote_not_in_source" in result["errors"]
    question["answer"]["claims"][0]["citations"][0]["quote"] = "from random import choice as ch"
    question["answer"]["claims"][0]["citations"][0].pop("source_id")
    assert "citation_has_no_source_id" in score_answer(case, question, source)["errors"]


def test_insufficient_case_rejects_unsupported_claims() -> None:
    case = {"expected": "insufficient"}
    question = {
        "status": "insufficient",
        "answer": {"insufficient_evidence": True, "claims": [{"text": "An invented date."}]},
    }

    assert "insufficient_answer_contains_claims" in score_answer(case, question, {})["errors"]
