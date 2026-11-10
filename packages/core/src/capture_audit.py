"""Capture audit (spec/platform/v2-fragment-knowledge-store.md §13.2 revision): after the worker records facts at the end
of a work unit, Jev checks every sentence / code line of the work transcript and returns what looks like durable
knowledge that no recorded fact carries yet. The worker answers each item (record via knowledge_record, or skip).

Decision question = union of three single questions (write-01 q_compare: Q0·Q2·Q4 union had the fewest misses, F1 .959):
  Q0 KQ noul, Q4 KQ + examples noul, Q2 content-kind choice (not_knowledge < .5).
Coverage question CQ (noul) against the facts already recorded. Layer (ddd..ssot) is attached as a kind tag only;
partitioning is NOT decided here (capture-01: domain = work area, not a layer name).
Jev access is injected (`judge`) so tests run offline."""
import json
import re
import time
from urllib.request import Request
import host_defaults

_KQ_BASE = ("items[{i}] 는 이번 작업의 한 문장(또는 코드 변경 요약 한 줄)이다. conversation 흐름상 이 문장이 미래 작업자가 알아야 할 "
      "제품·시스템 지식(확정된 결정·규칙·금지·동작·조건·코드 경로의 역할·테스트가 보호하는 것, 사용자가 요구·동의했거나 거부하지 않은 방침)을 "
      "담고 있는가? 인사·동의만 하는 말, 진행 상황·계획 안내, 검증 결과 보고, 되묻는 질문, 사용자가 거부한 제안은 false.")
_KQ_EXAMPLES = (" 예(true): 'Unit 행이 하나라도 있으면 자동 시딩을 멈춘다', '`X.test.ts` 는 순서를 보호한다', "
              "'마이그레이션이 아니라 신규 고객 기본값으로 한정한다'. 예(false): '좋아, 동의해', '테스트를 돌려 보겠습니다', '모두 통과했습니다'.")
# audit-01 (2026-09-29, live-01 false alarms on request-only turns): a request is knowledge when the work was carried out
# or it fixes target/value/scope/policy; unanswered asks, info questions, turn-only answer/working-style instructions and
# procedure chores are not. Request-set false alarms 7 -> 3 of 19, write-01 misses 14-15 -> 13 of 310 (no regression).
_REQ = (" 사용자 요청도 그대로 작업이 진행됐거나 대상·값·범위·방침을 구체적으로 정하면 true 다(무엇을 만들었는지·어느 대상인지의 지식). "
        "false 인 요청: 수행되지 않고 되묻기로 끝난 요청(예: 값 없이 '제한을 늘려줘' 뒤 '몇 자로?'), 정보를 묻는 질문, "
        "이번 턴의 답변·작업 방식만 정하는 지시(예: '코드는 바꾸지 말고 알려줘', '한국어로 답해줘', '다른 파일은 건드리지 마'), "
        "제품이 아니라 작업 절차만 시키는 요청(예: '테스트 돌려봐', 'PR 올려줘', 'README 를 요약해줘').")
KQ = _KQ_BASE + _REQ
KQ_EX = _KQ_BASE + _KQ_EXAMPLES + _REQ
NOTK = ("인사·동의만 하는 말·진행·계획 안내·검증 결과 보고·되묻는 질문·거부되거나 확정 안 된 제안·작업 방식 메타"
        "·사과나 자기 행동 설명(예: '통보만 했습니다', '물었어야 했습니다')"
        "·되묻기로 끝난 요청·이번 턴의 답변·작업 방식만 정하는 지시·작업 절차만 시키는 요청·정보를 묻는 질문")
KIND_Q = "items[{i}] 는 어떤 종류의 문장인가?"
KIND_C = {"rule": "지켜야 할 규칙·금지·조건·예외", "behavior": "화면·기능의 동작·반응·흐름 단계·값·순서",
          "decision": "확정된 결정과 그 이유·기각안·범위 한정", "code_role": "코드 경로·함수·파일의 역할", "test_guard": "테스트가 보호하는 동작",
          "term": "용어 정의", "not_knowledge": NOTK}
# Second check for USER sentences the union flagged (audit-01/persist.json, 2026-09-29): the union still took turn-only
# instructions ('한국어로 답해줘', '다른 파일은 건드리지 마') as knowledge. Keep when PQ >= PQ_KEEP. At 0.3: request-set false alarms
# 3 -> 0 of 19 with 0 of 13 knowledge lost; write-01 misses 13 -> 14 of 310 (noise level), false alarms 7 -> 4 of 49.
# 0.5 lost 6 real user requirements (misses 19), so the bar is low on purpose: drop only clear non-knowledge.
PQ = ("items[{i}] 는 사용자가 한 말이다. conversation 흐름상 이 말이 이번 요청이 끝난 뒤에도 남아야 할 제품·작업 지식인가? "
      "true: 값·대상·범위·금지·방침을 정하는 말(예: '1000자로 늘려줘', '앞으로 고객 이름은 로그에 남기지 마'), "
      "그대로 수행된 작업이 무엇을 만드는지·어느 대상인지 정하는 요청, 제시된 선택지 중 하나를 고르는 확정(예: 'a로 가자'). "
      "false: 인사, 정보를 묻는 질문, 되묻기로 끝난 값 없는 요청, 이번 답변·작업 방식만 정하는 지시(예: '한국어로 답해줘', "
      "'다른 파일은 건드리지 마', '코드는 바꾸지 말고 알려줘'), 작업 절차만 시키는 요청(예: '테스트 돌려봐', 'PR 올려줘').")
PQ_KEEP = 0.3
CQ = "items[{i}] 의 지식 내용이 facts 중 어느 하나 이상에 담겨 있는가? 조건·값·식별자까지 같아야 한다. 일부만 담겼거나 비슷한 주제만이면 false."
CHUNK = 20
MAX_UNITS = 400


def request_answers(cfg, body, http=None, *, max_attempts=1):
    http = http or host_defaults.lazy_judgement_http
    """One accounted HTTP request path shared by text batches and whole-state questions."""
    if max_attempts not in (1, 2): raise ValueError('max_attempts must be 1 or 2')
    data = None
    for attempt in range(max_attempts):
        try:
            req = Request(cfg['jev_base_url'].rstrip('/') + '/v1/systemone', data=json.dumps(body, ensure_ascii=False).encode(),
                          headers={'Authorization':'Bearer '+cfg['jev_api_key'],'Content-Type':'application/json'},method='POST')
            with http(req, timeout=120) as resp: data=json.load(resp)
            if set(data.get('answers',{})) != set(body['questions']): raise ValueError('answer keys mismatch')
            break
        except (OSError,ValueError,KeyError,TypeError):
            data=None
            if attempt+1<max_attempts: time.sleep(1)
    if data is None: raise RuntimeError(f'Jev judgement failed after {max_attempts} attempts')
    return data['answers']


def make_judge(cfg, http=None, *, max_attempts=1, text_limit=600):
    http = http or host_defaults.lazy_judgement_http
    """judge(state, texts, question, criteria=None) -> list[answer dict]; noul when criteria is None, else choice."""
    if max_attempts not in (1, 2):
        raise ValueError("max_attempts must be 1 or 2")
    if text_limit is not None and (not isinstance(text_limit, int) or text_limit < 1):
        raise ValueError("text_limit must be positive or None")

    def judge(state, texts, question, criteria=None):
        out = []
        for k in range(0, len(texts), CHUNK):
            chunk = texts[k:k + CHUNK]
            qs = {f"m{j}": {"type": "choice" if criteria else "noul", "instructions": question.format(i=j),
                            "criteria": criteria or {"true": "예", "false": "아니오"}} for j in range(len(chunk))}
            body = {"model": cfg["jev_model"], "state": {**state, "items": [{"i": j, "text": t if text_limit is None else t[:text_limit]} for j, t in enumerate(chunk)]},
                    "questions": qs}
            answers = request_answers(cfg,body,http,max_attempts=max_attempts)
            out += [answers[f"m{j}"] for j in range(len(chunk))]
        return out
    return judge


def split_units(transcript):
    """transcript: [{role: user|assistant|code, text}] -> list of (role, sentence). Code lines are kept whole."""
    units = []
    for m in transcript if isinstance(transcript, list) else []:
        if not isinstance(m, dict) or not isinstance(m.get("text"), str):
            continue
        role = m.get("role")
        if role == "code":
            units += [("code", l.strip("- ").strip()) for l in m["text"].splitlines() if len(l.strip("- ").strip()) > 3]
        elif role in ("user", "assistant"):
            units += [(role, s.strip()) for s in re.split(r"(?<=[.!?。])\s+|\n+", m["text"]) if len(s.strip()) > 3]
    return units


def _noul(a):
    return float(a.get("noul", 0.0))


def _not_knowledge(a):
    p = a.get("probabilities") or {}
    return float(p.get("not_knowledge", 1.0 if a.get("choice") == "not_knowledge" else 0.0))


# multilingual-e5 cosines are compressed: G7 turn sample (2026-09-28, e5-small) same decision .937-.973, different decisions <= .921.
# e5-large re-check (emb-01/dup-result.json, 2026-09-29): same .904-.962, different .821-.970 - overlap like e5-small; kept.
DUP_COSINE = 0.935


def dedupe(missing, encode=None):
    """Collapse near-identical missing items (the same decision said in the request, the plan and the report).
    Keeps the assistant's statement over the user's request and the longer text; embedding-free fallback keeps all."""
    if len(missing) < 2:
        return missing
    from embedding_thresholds import get
    cutoff = get('capture_dedupe')
    if encode is None:
        import embed
        encode = embed.encode_passages
    try:
        vectors = encode([m["text"] for m in missing])
    except Exception:
        return missing
    rank = sorted(range(len(missing)), key=lambda i: (missing[i]["from"] != "assistant", -len(missing[i]["text"])))
    kept = []
    for i in rank:
        if all(sum(a * b for a, b in zip(vectors[i], vectors[j])) < cutoff for j in kept):
            kept.append(i)
    return [missing[i] for i in sorted(kept)]


def audit(transcript, recorded_facts, judge, encode=None):
    units = split_units(transcript)
    if not units:
        return {"ok": True, "units": 0, "missing": [], "truncated": False}
    truncated = len(units) > MAX_UNITS
    units = units[-MAX_UNITS:]
    texts = [u for _, u in units]
    conv = "\n".join(f"{r}: {u}" for r, u in units)[-8000:]
    state = {"conversation": conv}
    q0 = judge(state, texts, KQ)
    q4 = judge(state, texts, KQ_EX)
    q2 = judge(state, texts, KIND_Q, KIND_C)
    facts = [f for f in (recorded_facts or []) if isinstance(f, str) and f.strip()]
    cq = judge({"facts": facts[-120:]}, texts, CQ) if facts else [{"noul": 0.0}] * len(texts)
    missing = []
    for i, (role, text) in enumerate(units):
        votes = [_noul(q0[i]) >= .5, _noul(q4[i]) >= .5, _not_knowledge(q2[i]) < .5]
        if any(votes) and _noul(cq[i]) < .5:
            p = q2[i].get("probabilities") or {}
            kind = max((k for k in p if k != "not_knowledge"), key=p.get, default=q2[i].get("choice"))
            missing.append({"id": f"u{i}", "from": role, "text": text, "kind": kind, "votes": sum(votes)})
    users = [k for k, m in enumerate(missing) if m["from"] == "user"]
    if users:
        pq = judge(state, [missing[k]["text"] for k in users], PQ)
        drop = {k for k, a in zip(users, pq) if _noul(a) < PQ_KEEP}
        missing = [m for k, m in enumerate(missing) if k not in drop]
    missing = dedupe(missing, encode)
    return {"ok": True, "units": len(units), "missing": missing, "truncated": truncated,
            "instruction": "For EVERY missing item: record it with knowledge_record (quote the original words verbatim; "
                           "user_confirmed only for non-question user confirmations). Skip ONLY when it is not knowledge "
                           "(greeting, bare agreement, progress/plan notice, verification report, question, unconfirmed proposal). "
                           "Do NOT skip because it seems already recorded: duplicates are removed later by digestion (is_new)."}
