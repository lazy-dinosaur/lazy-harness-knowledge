"""Korean rendering of a stored fact form (schema-delta '한국어로 바꿀 때 뜻 유지', 2026-09-30).

The form is canonical; Korean text is a view made on demand. Leaf sentences are kept as written (meaning leaked
when slots were reassembled, structure-01 pilots 1-4); only the joints change: the last word of a condition leaf gets a
connective ending (면 / 거나 / 고 / 더라도) by rule, no morphology library. Every rendering is checked by code:
leaf bodies, numbers, identifiers and negation must survive; when a rule cannot be applied or the check fails the fact
is shown in the plain labelled form instead (조건: … → 결과: …).
"""
import re

import fact_form


def _jong(ch):
    o = ord(ch) - 0xAC00
    return o % 28 if 0 <= o < 11172 else None


def _drop_jong(ch):
    o = ord(ch) - 0xAC00
    return chr(0xAC00 + o - o % 28)


JONG_N, JONG_L, JONG_SS, JONG_B = 4, 8, 20, 17
# irregular present forms whose stem a rule cannot recover (ㄹ/ㄷ/ㅂ stems)
IRREGULAR = {"만든다": "만들", "연다": "열", "판다": "팔", "분다": "불", "운다": "울", "논다": "놀", "건다": "걸", "민다": "밀", "푼다": "풀",
             "든다": "들", "언다": "얼", "단다": "달", "산다": "살", "끄다": "끌", "떠돈다": "떠돌", "돈다": "돌",
             "듣는다": "들으", "걷는다": "걸으", "묻는다": "물으", "싣는다": "실으", "돕는다": "도우", "줍는다": "주우",
             "쉽다": "쉬우", "어렵다": "어려우", "가깝다": "가까우", "무겁다": "무거우", "가볍다": "가벼우",
             "새롭다": "새로우", "어두우다": "어두우"}
# adjectives whose stem ends without a final consonant (otherwise '…다' after a vowel is read as the copula)
VOWEL_ADJ = {"크", "빠르", "느리", "나쁘", "다르", "바쁘", "아프", "예쁘", "기쁘", "이르", "흐리", "어리", "두렵", "지나치",
             "모자라", "같", "틀리", "보이", "달라지", "사라지", "나타나", "바뀌", "끊기", "닫히", "열리"}


def stem(word):
    """Predicate stem of a dictionary/present/past form ending in 다, or None when no rule applies."""
    w = word.rstrip(".")
    if not w.endswith("다") or len(w) < 2:
        return None
    if w in IRREGULAR:
        return IRREGULAR[w]
    body, pen = w[:-1], w[-2]
    j = _jong(pen)
    if j is None:  # 'OFF다', '1다', '`x`다': the copula after a non-Hangul noun
        return body + "이" if re.match(r"[A-Za-z0-9)`\]]", pen) else None
    if w.endswith("는다") and len(w) >= 3 and _jong(w[-3]) not in (None, 0):
        return w[:-2]                                   # 먹는다 -> 먹
    if j == JONG_N:
        return body[:-1] + _drop_jong(pen)              # 열린다 -> 열리, 한다 -> 하, 된다 -> 되
    if j == JONG_SS or pen in ("있", "없", "않", "이"):
        return body                                     # 했다 -> 했, 있다 -> 있, 다른 것이다 -> …이
    if j == 0:
        if pen == "하" or body in VOWEL_ADJ or body[-2:] in VOWEL_ADJ or body[-3:] in VOWEL_ADJ:
            return body                                 # 필요하다 -> 필요하, 크다 -> 크
        return body + "이"                            # copula after a vowel noun: 상태다 -> 상태이
    if j == JONG_B and body in ("좁", "입", "잡", "뽑", "씹", "입"):
        return body
    return body                                         # 같다 -> 같, 길다 -> 길, 적다 -> 적


def _eu(st):
    """'으' before 면 after a final consonant other than ㄹ."""
    j = _jong(st[-1])
    return "으" if j not in (None, 0, JONG_L) else ""


def ending(leaf, kind):
    """leaf with its last predicate turned into a connective: kind in 면|거나|고|더라도. None when no rule applies."""
    text = leaf.strip().rstrip(".")
    head, _, last = text.rpartition(" ")
    st = stem(last)
    if not st:
        return None
    tail = {"면": _eu(st) + "면", "거나": "거나", "고": "고", "더라도": "더라도"}[kind]
    return (head + " " if head else "") + st + tail


class Fallback(Exception):
    pass


def _cond(e, final):
    """Condition expression -> Korean clause ending with `final` (면 or 더라도). Mixed nesting is not rendered."""
    if "leaf" in e:
        out = ending(e["leaf"], final)
        if out is None:
            raise Fallback(e["leaf"])
        return out
    args = e["args"]
    if any("leaf" not in a for a in args):
        raise Fallback("nested condition")
    join = "거나" if e["op"] == "OR" else "고"
    parts = []
    for k, a in enumerate(args):
        out = ending(a["leaf"], final if k == len(args) - 1 else join)
        if out is None:
            raise Fallback(a["leaf"])
        parts.append(out)
    return " ".join(parts)


def _sentence(s):
    s = s.strip()
    return s if s.endswith((".", "?", "!")) else s + "."


def _labelled(form):
    parts = []
    if form.get("kind") == "if":
        parts.append("조건: " + fact_form.expr_text(form["if"]).replace(" OR ", " 또는 ").replace(" AND ", " 그리고 "))
        if form.get("even_if"):
            parts.append("이 경우에도: " + fact_form.expr_text(form["even_if"]).replace(" OR ", " 또는 ").replace(" AND ", " 그리고 "))
    elif form.get("kind") in ("before", "after"):
        parts.append(("이것보다 먼저: " if form["kind"] == "before" else "이것 다음에: ") + form["anchor"])
    joiner = " 또는 " if form.get("join") == "OR" else " / "
    parts.append("결과: " + joiner.join(form["then"]))
    if form.get("except"):
        parts.append("예외: " + fact_form.expr_text(form["except"]).replace(" OR ", " 또는 ").replace(" AND ", " 그리고 "))
    if form.get("because"):
        parts.append("이유: " + form["because"])
    return " → ".join(parts)


NUM = re.compile(r"\d+(?:[.,]\d+)?")
TICK = re.compile(r"`[^`]+`")
NEG = re.compile(r"(않|없|아니|금지|못)")


def _body(leaf):
    t = leaf.strip().rstrip(".")
    head, _, last = t.rpartition(" ")
    return head


TOPIC = re.compile(r"^(\S.{0,60}?(?:은|는)) ")


def _drop_topic(cond_leaf, leaf):
    """'사람은 부서에 들어오면 사람은 접근을 얻는다' reads as Korean only without the repeated topic: drop the result's
    topic when the last condition leaf starts with the same one (meaning unchanged: the topic carries over)."""
    a, b = TOPIC.match(cond_leaf or ""), TOPIC.match(leaf)
    if a and b and a.group(1) == b.group(1):
        return leaf[b.end():], b.group(1)
    return leaf, None


def verify(form, text, dropped=()):
    """Code check that the rendering kept every leaf body (less a dropped repeated topic), number, identifier, negation."""
    leaves = fact_form.leaves(form)
    src = " ".join(l["text"] for l in leaves) + " " + (form.get("because") or "")
    flat = re.sub(r"\s+", "", text)
    for l in leaves:
        b = _body(l["text"])
        for d in dropped:
            if b.startswith(d + " "):
                b = b[len(d) + 1:]
        if b and re.sub(r"\s+", "", b) not in flat:
            return False
    for pat in (NUM, TICK):
        for x in set(pat.findall(src)):
            if x not in text:
                return False
    return len(NEG.findall(src)) <= len(NEG.findall(text))


def view(text):
    """What a person or the parent reads: a plain one-sentence fact as stored, anything with operators in Korean."""
    if not isinstance(text, str):
        return text
    form = fact_form.parse(text)
    if form.get("kind") == "plain" and len(form.get("then", [])) == 1 and not form.get("because") and not form.get("except"):
        return text
    return to_korean(form)[0]


def to_korean(form):
    """-> (text, how) with how 'natural' or 'labelled'."""
    try:
        kind = form.get("kind", "plain")
        then = form["then"]
        if form.get("join") == "OR" and len(then) > 1:
            parts = [ending(t, "거나") for t in then[:-1]]
            if None in parts:
                raise Fallback("or")
            results = [_sentence(" ".join(parts) + " " + then[-1].strip().rstrip("."))]
        else:
            results = [_sentence(t) for t in then]
        dropped = []
        if kind == "if":
            last_cond = fact_form._expr_leaves(form["if"])[-1]
            if form.get("join") != "OR":
                for k_, t_ in enumerate(then):  # every result under the condition drops the repeated topic
                    first, d = _drop_topic(last_cond, t_)
                    if d:
                        results[k_] = _sentence(first)
                        dropped.append(d)
            lead = _cond(form["if"], "면")
            if form.get("even_if"):
                lead = _cond(form["even_if"], "더라도") + " " + lead
            body = lead + " " + results[0]
            if len(results) > 1:
                # review P1 (2026-10-01): 'C면 A다. B다.' reads B as unconditional — each result repeats the condition
                body = " ".join(lead + " " + r for r in results)
        elif kind == "after":
            body = _sentence(form["anchor"]) + " 그 뒤 " + " ".join(results)
        elif kind == "before":
            body = "먼저 " + " ".join(results) + " 그다음 " + _sentence(form["anchor"])
        else:
            body = " ".join(results)
        if form.get("except"):
            body += " 단, " + _cond(form["except"], "면") + " 예외다."
        if form.get("because"):
            body += " (이유: " + form["because"].strip().rstrip(".") + ")"
        if not verify(form, body, dropped):
            raise Fallback("verify")
        return body, "natural"
    except (Fallback, KeyError, TypeError):
        return _labelled(form), "labelled"
