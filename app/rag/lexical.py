"""Language-neutral lexical terms for PostgreSQL full-text search.

PostgreSQL's ``simple`` configuration splits only on whitespace and punctuation, so a Chinese
sentence becomes one huge lexeme and never matches a question. We tokenize in Python instead:
letters and digits of other scripts become casefolded words, and each run of CJK, kana or
hangul becomes overlapping character bigrams (an isolated character stays a unigram). Chunks
store the space-joined terms; questions become an OR query over the same terms, ranked by
``ts_rank_cd`` so chunks that match more and denser terms rank higher.

Changing this module changes stored terms: bump ``LEXICAL_VERSION`` and backfill
``document_chunks.search_text`` in a migration.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator

LEXICAL_VERSION = "cjk-bigram-v1"

_CJK_RANGES = (
    "\u3040-\u30ff"  # Hiragana, Katakana
    "\u3400-\u4dbf"  # CJK Extension A
    "\u4e00-\u9fff"  # CJK Unified Ideographs
    "\uac00-\ud7af"  # Hangul syllables
    "\uf900-\ufaff"  # CJK compatibility ideographs
)
_TOKEN = re.compile(rf"[{_CJK_RANGES}]+|(?:(?![{_CJK_RANGES}])[^\W_])+")
_CJK = re.compile(rf"[{_CJK_RANGES}]")

MAX_QUERY_TERMS = 48

# Question scaffolding that would otherwise match almost every chunk.
_ENGLISH_STOP = frozenset(
    """a an the is are was were be been being of to in on for and or not with without what
    which how why when where who whom whose does do did can could should would will shall
    this that these those it its as by from at about into than then there their them they
    i me my we our you your he she his her please explain describe define tell give list
    show between difference differences mean means meaning example examples""".split()
)
_CJK_STOP = frozenset(
    """什么 么是 是什 如何 怎么 怎样 么样 为什 哪些 哪个 请问 请解 解释 一下 说明 介绍
    这个 那个 这些 那些 一个 可以 是否 有什 有哪 能否 的是 是指 指的 吗 呢 吧 的 了 是""".split()
)


def _terms(text: str) -> Iterator[str]:
    normalized = unicodedata.normalize("NFKC", text).casefold()
    for match in _TOKEN.finditer(normalized):
        token = match.group()
        if _CJK.match(token):
            if len(token) == 1:
                yield token
            else:
                for start in range(len(token) - 1):
                    yield token[start : start + 2]
        else:
            yield token


def lexical_document(text: str) -> str:
    """Terms stored for a chunk; every term contains only letters, digits or CJK characters."""
    return " ".join(_terms(text))


def lexical_query_terms(text: str) -> list[str]:
    terms: dict[str, None] = {}
    for term in _terms(text):
        if term in _ENGLISH_STOP or term in _CJK_STOP:
            continue
        if term.isascii() and len(term) == 1 and not term.isdigit():
            continue
        terms.setdefault(term)
        if len(terms) >= MAX_QUERY_TERMS:
            break
    return list(terms)


def lexical_query(text: str) -> str | None:
    """``to_tsquery`` input matching any term; ``None`` when nothing searchable remains.

    Terms are restricted to letters, digits and CJK characters, so no tsquery operator or
    quote can be injected through user text.
    """
    terms = lexical_query_terms(text)
    return " | ".join(terms) if terms else None
