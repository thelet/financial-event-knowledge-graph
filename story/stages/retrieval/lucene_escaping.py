"""Turning a model-supplied term list into a Lucene query that can only mean "these words".

Responsibility: one function's worth of escaping, and the reason it is not one line. The
`passage_text` fulltext index is a Lucene index, and `db.index.fulltext.queryNodes` hands the
string straight to Lucene's query parser — so a term list is *syntax*, not data, unless
something makes it data first. This module is that something. It knows nothing about Neo4j,
nothing about the graph, and is testable with no database, which is why it is its own file
rather than four helpers inside the retriever.

**Two failure modes, and only one of them is about characters.**

*Characters.* §9 lists `+ - && || ! ( ) { } [ ] ^ " ~ * ? : \\ /` as reserved. `:` is the one
that matters most: unescaped, `text:foo` is field-prefix injection, and every passage id in
this corpus contains three colons (`norm:0001801169:…`). The escape set below is Lucene's own
`QueryParser.escape` set, which is a superset of §9's — it adds `&` and `|` individually
rather than only the two-character `&&`/`||`, because escaping the pair but not the members
leaves `a & b` parsing as something the caller did not write.

*Words.* **`AND`, `OR`, `NOT` and `TO` are operators and are not characters**, so no amount of
character escaping touches them. A `terms[]` of `["margin", "NOT", "gross"]` becomes a boolean
exclusion: the model has changed the query's *meaning*, and it has done so in the direction of
suppressing evidence, which is precisely the failure §11 and §13 exist to prevent. It cannot
escape `Passage.text` and it cannot write, so the severity is low — but a silent
evidence-suppression channel controlled by the thing being verified is not a thing to leave
open because it is cheap. A reserved word is quoted, which makes it a term for that word.

**Why a whitespace-bearing term becomes a phrase.** `"Adjusted EBITDA"` is one search
intention, and quoting it does two jobs at once: it searches the pair adjacently, and it makes
every operator inside it inert — so `["gross margin NOT adjusted"]` cannot smuggle an operator
past the word check by hiding it mid-term. Inside a phrase only `"` and `\\` still need
escaping; Lucene reads the rest literally.

**Terms are joined by a space, never by an operator.** The `passage_text` index's default
operator is `OR`, so a space is recall — the union of the terms. This module emits no `AND`,
no `OR` and no `NOT` of its own, which is what makes "the query cannot exclude anything" a
property of the code rather than a convention.
"""

from __future__ import annotations

from typing import Sequence

#: Lucene's `QueryParser.escape` set, a superset of §9's list. `\\` is first in the tuple only
#: for readability — the implementation walks characters, so a backslash is escaped once and
#: cannot be double-escaped by a later pass, which a `str.replace` chain gets wrong.
RESERVED_CHARACTERS = frozenset('\\+-!():^[]"{}~*?|&/')

#: Operators that are words. **Matched case-insensitively, which is deliberately wider than
#: Lucene's own rule**: the default `QueryParser` honours only the upper-case spelling, so
#: quoting `not` protects against nothing today. It costs one pair of quotes — a quoted
#: single word is a term search for that word, the same result — and it removes a dependency
#: on a parser setting this code does not own and cannot see. Where the cost of being wrong is
#: a silent evidence exclusion, matching the wider set is the cheaper mistake.
RESERVED_WORDS = frozenset({"AND", "OR", "NOT", "TO"})

#: What must still be escaped inside a quoted phrase. Everything else is literal there.
_PHRASE_ESCAPES = frozenset('\\"')


def escape_term(term: str) -> str:
    """One caller-supplied term as a Lucene term that can only match itself.

    Returns `""` for a term that is empty or only whitespace — the caller drops it rather than
    emitting a bare `""`, which Lucene parses as an empty phrase and which would match
    nothing while looking like a search.
    """
    stripped = term.strip()
    if not stripped:
        return ""
    if stripped.upper() in RESERVED_WORDS or _has_whitespace(stripped):
        return '"' + _escape(stripped, _PHRASE_ESCAPES) + '"'
    return _escape(stripped, RESERVED_CHARACTERS)


def build_query(terms: Sequence[str]) -> str:
    """The whole query string: every term escaped, joined by a space, nothing else.

    No parentheses, no field prefix, no operator. A caller that wants intersection semantics
    is asking for a different tool, not for this function to grow a flag — the moment this
    emits an operator, whether the query can suppress evidence stops being answerable by
    reading it.
    """
    return " ".join(escaped for escaped in (escape_term(term) for term in terms) if escaped)


def _escape(value: str, characters: frozenset[str]) -> str:
    return "".join(f"\\{character}" if character in characters else character
                   for character in value)


def _has_whitespace(value: str) -> bool:
    return any(character.isspace() for character in value)


__all__ = ["RESERVED_CHARACTERS", "RESERVED_WORDS", "build_query", "escape_term"]
