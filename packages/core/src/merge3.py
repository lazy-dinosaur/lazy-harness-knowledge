"""Three-way merge of one fragment sentence (git merge semantics, word level): base = the revision the worker read,
theirs = the canon now (another work unit changed it), mine = this unit's rewrite. Non-overlapping edits are both
kept; overlapping edits are a conflict for the user. No LLM, no guessing."""
import difflib
import re

_TOKEN = re.compile(r"\s+|[^\s\w]|\w+", re.UNICODE)


def _tokens(text):
    return _TOKEN.findall(text or "")


def _edits(base, other):
    """[(i1, i2, replacement tokens)] over base tokens."""
    return [(i1, i2, other[j1:j2]) for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, base, other, autojunk=False).get_opcodes()
            if tag != "equal"]


def merge(base, theirs, mine):
    """(merged text, None) when the edits do not overlap, else (None, 'conflict')."""
    if theirs == base or theirs == mine:
        return mine, None
    if mine == base:
        return theirs, None
    b, t, m = _tokens(base), _tokens(theirs), _tokens(mine)
    et, em = _edits(b, t), _edits(b, m)
    for i1, i2, rep in et:
        for k1, k2, rep2 in em:
            touch = (i1 < k2 and k1 < i2) or (i1 == i2 == k1 == k2) or (i1 == i2 and k1 < i1 < k2) or (k1 == k2 and i1 < k1 < i2)
            if touch and not (i1 == k1 and i2 == k2 and rep == rep2):
                return None, "conflict"
    out, pos = [], 0
    for i1, i2, rep in sorted({(a, c, tuple(r)) for a, c, r in et + em}, key=lambda e: (e[0], e[1])):
        if i1 < pos:
            continue  # identical edit already applied
        out += b[pos:i1] + list(rep)
        pos = i2
    out += b[pos:]
    return "".join(out), None
