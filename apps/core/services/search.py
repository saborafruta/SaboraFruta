from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


_NON_ALNUM_RE = re.compile(r'[^a-z0-9]+')


def normalize_search_text(value: Any) -> str:
    """Normaliza caixa, acentos, cedilha e pontuacao para comparacao."""

    decomposed = unicodedata.normalize('NFKD', str(value or ''))
    without_marks = ''.join(char for char in decomposed if not unicodedata.combining(char))
    return _NON_ALNUM_RE.sub(' ', without_marks.casefold()).strip()


def _candidate_rank(
    candidate: Mapping[str, Any],
    query: str,
    *,
    name_fields: Sequence[str],
    code_fields: Sequence[str],
    identifier_field: str,
) -> tuple[int, str, int] | None:
    normalized_query = normalize_search_text(query)
    if not normalized_query:
        return None

    compact_query = normalized_query.replace(' ', '')
    identifier = str(candidate.get(identifier_field) or '').strip()
    if compact_query.isdigit() and identifier == compact_query:
        return 0, identifier, 0

    for field in code_fields:
        code = normalize_search_text(candidate.get(field)).replace(' ', '')
        if not code:
            continue
        if code == compact_query:
            return 0, code, 0
        if code.startswith(compact_query):
            return 1, code, len(code)

    query_terms = normalized_query.split()
    best_rank = None
    for field in name_fields:
        name = normalize_search_text(candidate.get(field))
        if not name:
            continue
        words = name.split()
        if len(query_terms) == 1 and len(query_terms[0]) == 1:
            matches = bool(words and words[0].startswith(query_terms[0]))
        else:
            matches = all(any(word.startswith(term) for word in words) for term in query_terms)
        if not matches:
            continue

        if name == normalized_query:
            rank = 2
        elif name.startswith(normalized_query):
            rank = 3
        else:
            rank = 4
        current = rank, name, len(name)
        if best_rank is None or current < best_rank:
            best_rank = current
    return best_rank


def ranked_search_ids(
    candidates: Iterable[Mapping[str, Any]],
    query: str,
    *,
    name_fields: Sequence[str],
    code_fields: Sequence[str] = (),
    identifier_field: str = 'pk',
    limit: int = 20,
) -> list[Any]:
    """Retorna IDs ordenados por relevancia, sem casamento no meio da palavra."""

    ranked = []
    for candidate in candidates:
        rank = _candidate_rank(
            candidate,
            query,
            name_fields=name_fields,
            code_fields=code_fields,
            identifier_field=identifier_field,
        )
        if rank is not None:
            ranked.append((rank, candidate.get(identifier_field)))

    ranked.sort(key=lambda item: (*item[0], str(item[1])))
    return [identifier for _, identifier in ranked[:limit]]
