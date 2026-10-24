"""Text normalisation and hashing shared by the importer and the parsers.

Dependency-free on purpose: the offline paper-inspection tooling imports this
without needing the ORM or a database driver.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

_WS_RE = re.compile(r"\s+")


def normalise_text(value: str) -> str:
    """Tidy whitespace and invisible characters, preserving every glyph.

    Uses NFC, *not* NFKC. NFKC is a compatibility fold: it rewrites ``x²`` to
    ``x2``, ``½`` to ``1/2`` and ``ﬁ`` to ``fi``. That silently corrupts the
    mathematics papers, so it must never be applied to text that is stored and
    shown to candidates. NFC only composes combining marks, which is safe and
    makes Tamil and other regional scripts compare reliably.
    """
    value = unicodedata.normalize("NFC", value or "")
    value = value.replace(" ", " ").replace("​", "").replace("﻿", "")
    return _WS_RE.sub(" ", value).strip()


def question_md5(text: str) -> str:
    """MD5 duplicate key for a question.

    Here the aggressive NFKC fold *is* wanted: two imports of the same
    question that differ only in a superscript or a ligature should collide,
    so the second is caught as a duplicate. Only the hash is folded; the
    stored text keeps its original glyphs.
    """
    folded = unicodedata.normalize("NFKC", normalise_text(text)).casefold()
    return hashlib.md5(folded.encode("utf-8")).hexdigest()
