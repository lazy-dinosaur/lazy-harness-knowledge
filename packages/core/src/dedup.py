"""One duplicate test for digestion (schema-delta '중복 판정 함수 분리', 2026-09-29).
Compared against what digestion is about to make canonical in the same pass (not the ledger: pending entries may still
be rejected). Precision first: dropping a real fact is worse than a leftover duplicate (write-01 round_dedup), so only
the same sentence counts (whitespace, punctuation, filler '최대/모두/현재' and the sentence ending ignored).
Near matches were measured and rejected: '생성 때 … 자른다' vs '수정 때 … 자른다' and '자른다' vs '자르지 않는다' score
0.86-0.88 character ratio with the same numbers and e5-large cosine above 0.96, so cosine/ratio thresholds would drop
contradictory facts. Paraphrases stay for the later clean-up job."""
import re

_FILLER = re.compile(r"(최대|모두|현재)")
_ENDING = re.compile(r"(이다|입니다|한다|된다|다)$")


def normalize(text):
    """astra review P0-4 (2026-10-01): the decimal point and qualifiers (최대/모두/현재) carry meaning — '1.5' is not '15',
    '최대 3회' is not '3회'. Only spacing, quotes, brackets and the sentence ending are ignored."""
    # astra direction review P0 (2026-10-01): '`a.b`' and '`ab`' were equal. Code spans are kept verbatim and a dot
    # between word characters (domain_router.py, 1.5) is kept.
    parts = re.split(r"(`[^`]*`)", str(text or ""))
    out = []
    for part in parts:
        if part.startswith("`") and part.endswith("`") and len(part) >= 2:
            out.append(re.sub(r"\s+", "", part[1:-1]))  # inside a code span every character but spacing counts
            continue
        t = re.sub(r"(?<=\w)\.(?=\w)", "§", part)
        out.append(re.sub(r"[\s'\"·,.!?()\[\]]+", "", t))
    return _ENDING.sub("", "".join(out))


def _shape(text):
    """Operator fact -> (condition key, join, sorted normalized result sentences); None when it does not parse."""
    import fact_form
    if not isinstance(text, str) or fact_form.check(text):
        return None
    form = fact_form.parse(text)
    leaves = sorted(normalize(t) for t in form.get("then", []))
    if not all(leaves):
        return None
    return fact_form.condition_key(form), form.get("join"), form.get("because") and normalize(form["because"]), tuple(leaves)


def same_fact(a, b):
    """Same sentence after normalize(), or the same structure: equal condition and the same result sentences in any
    order ('IF A THEN B AND C' == 'IF A THEN C AND B'). A different condition or any different leaf is not the same."""
    na, nb = normalize(a), normalize(b)
    if na and na == nb:
        return True
    sa, sb = _shape(a), _shape(b)
    return sa is not None and sa == sb


def find_duplicate(text, candidates):
    """Index of the first candidate that is the same fact (same_fact), else None."""
    if not normalize(text):
        return None
    for i, c in enumerate(candidates or []):
        if same_fact(text, c):
            return i
    return None
