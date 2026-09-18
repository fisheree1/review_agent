---
name: review-agent-rag-quiz
description: Implement, tune, or review Review Agent RAG retrieval, cited answers, controlled Agent workflows, prompt versions, or Quiz generation and evaluation. Use when correctness depends on evidence, retrieval scope, structured model output, or Quiz quality.
---

# Review Agent RAG and Quiz

Optimize for trustworthy learning outcomes, not fluent output alone.

## Required context

Read the RAG, Agent, and Quiz sections in `../../../docs/product-requirements.md` and `../../../docs/architecture.md`. Read chunk, citation, and Quiz entities in `../../../docs/database-design.md` for persistence work.

## Invariants

- Apply workspace, selected-document, active-version, and deletion filters inside retrieval before content reaches a model.
- Version chunking, embedding, retrieval parameters, prompts, schemas, and models so an output can be reproduced.
- Treat document text as untrusted. It cannot change tool permissions or system policy.
- Bound Agent steps, time, tokens, cost, retries, and tool inputs. Use typed allowlisted tools.
- Attach citations to specific document versions and chunks. Reject unsupported factual claims or return insufficient evidence.
- Build Quiz through blueprint, evidence, structured generation, deterministic validation, then publication. Reject missing/ambiguous answers, duplicate options, unsupported questions, and invalid sources.

## Focused evaluation

Use a small versioned set of representative documents and high-value questions. Measure only outcomes that can change a product decision:

- retrieval finds the expected source within the allowed scope;
- cross-workspace or deselected sources never appear;
- insufficient evidence produces a refusal/qualification;
- citations resolve to text that supports the claim;
- Quiz output matches requested count/type/difficulty when evidence permits;
- every published question has a valid answer and supporting source.

Compare quality, latency, tokens, and cost against the current baseline. Do not test exact prose or every prompt token. Do not accept a parameter change based on a single anecdotal example.
