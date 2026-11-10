"""Leaf-level three-way merge of an operator fact (schema-delta '잎 단위 3자 병합', 2026-09-30).

base = the revision the worker read, theirs = the canon now (another work unit changed it), mine = this unit's rewrite.
The fact is split into parts: the condition (kind, if, even_if, anchor), the result sentences, except, because. Each
part merges git-style (one side changed -> take it; both changed the same way -> take it). Result sentences merge one by
one when the three lists are the same length, otherwise as sets (removals and additions of both sides). Only the same
part or the same result sentence changed differently on both sides is a conflict, and a sentence first tries the word
merge (merge3) before that. Text without a form, or a merged form that does not pass fact_form.check, falls back to the
word-level merge of the whole sentence.
"""
import fact_form
import merge3


class Conflict(Exception):
    pass


def _pick(base, theirs, mine):
    if theirs == mine:
        return mine
    if mine == base:
        return theirs
    if theirs == base:
        return mine
    raise Conflict


def _leaf(base, theirs, mine):
    try:
        return _pick(base, theirs, mine)
    except Conflict:
        merged, err = merge3.merge(base, theirs, mine)
        if err:
            raise
        return merged


def _then(base, theirs, mine):
    if len(base) == len(theirs) == len(mine):
        return [_leaf(b, t, m) for b, t, m in zip(base, theirs, mine)]
    B, T, M = set(base), set(theirs), set(mine)
    gone_t, gone_m = B - T, B - M
    new_t, new_m = [x for x in theirs if x not in B], [x for x in mine if x not in B]
    if (gone_t & gone_m) and new_t and new_m and new_t != new_m:
        raise Conflict  # the same sentence rewritten on both sides
    if (gone_t & gone_m) and bool(new_t) != bool(new_m):
        raise Conflict  # deleted on one side, rewritten on the other (review P1, 2026-10-01): not both
    out = [x for x in base if x not in gone_t and x not in gone_m]
    for x in new_t + new_m:
        if x not in out:
            out.append(x)
    if not out:
        raise Conflict
    return out


def _condition(f):
    return {k: f.get(k) for k in ("kind", "if", "even_if", "anchor")}


def to_text(form):
    """Form -> operator text (the inverse of fact_form.parse for well-formed facts)."""
    kind = form.get("kind", "plain")
    joiner = " OR " if form.get("join") == "OR" else " AND "
    out = joiner.join(t.strip().rstrip(".") for t in form["then"])
    if kind == "if":
        head = "IF " + fact_form.expr_text(form["if"])
        if form.get("even_if"):
            head += " EVEN IF " + fact_form.expr_text(form["even_if"])
        out = head + " THEN " + out
    elif kind in ("before", "after"):
        out = kind.upper() + " " + form["anchor"] + " THEN " + out
    if form.get("except"):
        out += " EXCEPT WHEN " + fact_form.expr_text(form["except"])
    if form.get("because"):
        out += " BECAUSE " + form["because"]
    return out


def merge_forms(base, theirs, mine):
    """(merged form, None) or (None, 'conflict')."""
    try:
        cond = _pick(_condition(base), _condition(theirs), _condition(mine))
        out = {k: v for k, v in cond.items() if v}
        out["then"] = _then(base["then"], theirs["then"], mine["then"])
        join = _pick(base.get("join"), theirs.get("join"), mine.get("join"))
        if join and len(out["then"]) > 1:
            out["join"] = join
        for k in ("except", "because"):
            v = _pick(base.get(k), theirs.get(k), mine.get(k))
            if v:
                out[k] = v
        return out, None
    except Conflict:
        return None, "conflict"


def merge(base, theirs, mine):
    """Same contract as merge3.merge: (merged text, None) or (None, 'conflict')."""
    if theirs == base or theirs == mine:
        return mine, None
    if mine == base:
        return theirs, None
    if all(isinstance(x, str) and not fact_form.check(x) for x in (base, theirs, mine)):
        form, err = merge_forms(fact_form.parse(base), fact_form.parse(theirs), fact_form.parse(mine))
        if err:
            return None, err
        text = to_text(form)
        if not fact_form.check(text) and fact_form.parse(text) == form:
            return text, None
    return merge3.merge(base, theirs, mine)
