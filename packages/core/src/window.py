"""Knowledge window (harness rule layer, schema-delta '지식 창 구성'): the relevant knowledge for the current work,
re-attached to every model request by the harness extension instead of piling up in the transcript.

Jev selects what the work needs (worker_tools._collect with NEED_Q); constraints and decisions go in first, facts after;
what does not fit is listed as a 'more' index. Size follows relevance (only Jev-selected fragments, never padding),
capped at CAP tokens."""
import json
from uuid import uuid4

import worker_tools

PRIORITY = {"constraint": 0, "decision": 1, "rationale": 2, "rejected": 3, "fact": 4, "procedure": 5, "term": 6, "question": 7}
CAP = 5000
HEADER = ("[knowledge-window] 지금 작업과 관련된 프로젝트 지식 (하네스가 자동으로 붙임, 요청마다 갱신). "
          "지식은 의도, 코드는 현실 — 다르면 사용자에게 확인. 더 필요하면 knowledge_search / knowledge_more. "
          "각 조각은 [alias@revision] — 그 지식을 바꾸면 knowledge_record 의 update/deprecate target_ref 에 그대로 적는다.")


def tokens(text):
    """Rough token estimate for mixed Korean/code text (about two characters per token)."""
    return max(1, len(text) // 2)


def build(dsn, host, question, queries, ask, cap=CAP):
    if not isinstance(question, str) or not question.strip():
        raise ValueError("question required")
    if not 200 <= cap <= 20000:
        raise ValueError("cap out of range")
    question = question.strip()[:1200]
    qs = [q.strip()[:300] for q in (queries or []) if isinstance(q, str) and q.strip()][:7]
    if question[:300] not in qs:
        qs.append(question[:300])
    _note, relevant = worker_tools._collect(dsn, host, question, qs, ask, worker_tools.NEED_Q)
    order = sorted(range(len(relevant)), key=lambda i: (PRIORITY.get(relevant[i].get("kind"), 9), i))
    picked, used = [], tokens(HEADER)
    for i in order:
        cost = tokens(relevant[i]["text"]) + 8
        if used + cost <= cap:
            picked.append(relevant[i])
            used += cost
    dropped = len(relevant) - len(picked)
    if not picked:
        return {"text": "", "aliases": [], "tokens": 0, "relevant": 0, "dropped": 0}
    more_d, more_g = worker_tools._more_index(dsn, host, picked)
    search_id = uuid4().hex[:12]
    (worker_tools.search_dir() / f"{search_id}.json").write_text(json.dumps(
        {"host_id": host, "question": question, "returned": [str(f["id"]) for f in picked]}))
    lines = [HEADER, ""] + worker_tools._render(picked)
    if dropped or more_d or more_g:
        lines += ["## 더 있음", ""]
        if dropped:
            lines.append(f"- 관련 있지만 창에 못 담은 조각 {dropped}개 — knowledge_search 로 받음")
        lines += [f"- 영역 {d}: {n}개 더" for d, n in sorted(more_d.items(), key=lambda kv: -kv[1])]
        lines += [f"- 묶음 {g}: {n}개 더" for g, n in sorted(more_g.items(), key=lambda kv: -kv[1])[:10]]
        lines += ["", f"search_id: {search_id} (knowledge_more 에 쓴다)"]
    text = "\n".join(lines) + "\n"
    return {"text": text, "aliases": [f["alias"] for f in picked], "tokens": tokens(text), "relevant": len(relevant),
            "dropped": dropped, "search_id": search_id}
