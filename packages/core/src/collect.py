"""Bounded, oracle-judged topic collection; no production judge is provided here."""
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

import store_pg as pg


class JudgePageFailure(Exception):
    """The page was read, but its judgement failed after the allowed retries."""


def collect_topic(dsn, host, queries: list[str], page_size=30, max_pages_per_query=5,
                  max_total=600, judge=None, variant="ctx-v2", diagnostics=None, plan_queries=()):
    if not queries or not all(isinstance(q, str) and q.strip() for q in queries):
        raise ValueError("nonempty queries required")
    if not all(isinstance(n, int) and n > 0 for n in (page_size, max_pages_per_query, max_total)):
        raise ValueError("limits must be positive integers")
    if not callable(judge):
        raise ValueError("a judge(query, candidates) is required")
    pg._variant(variant)
    lock = Lock()
    seen, relevant, traces = set(), [], [None] * len(queries)
    discovery = {}
    provenance = {}
    added = {}

    def run(index, query):
        pages = read = new = 0
        while True:
            with lock:
                if len(seen) >= max_total:
                    reason = "max_total"
                    break
                excluded = set(seen)
                remaining = min(page_size, max_total - len(seen))
            candidates = pg.search(dsn, host, query, remaining, mode="hybrid",
                                   exclude_ids=excluded, variant=variant, expand=False)
            # A concurrent query can claim candidates while the DB call is in flight.
            # Retry with its new exclusion set rather than consuming a duplicate page.
            with lock:
                if any(c["id"] in seen for c in candidates):
                    continue
                if len(seen) >= max_total:
                    reason = "max_total"
                    break
                candidates = candidates[:max_total - len(seen)]
                for c in candidates:
                    discovery[c["id"]] = len(discovery)
                    provenance[str(c["id"])] = {"query": query, "page": pages + 1,
                        "stage": "plan-query" if query in plan_queries else "page/judge"}
                    seen.add(c["id"])
                pages += 1
                read += len(candidates)
            if not candidates:
                reason = "exhausted"
                break
            try:
                verdicts = judge(query, candidates)
            except JudgePageFailure:
                with lock:
                    for c in candidates:
                        provenance[str(c['id'])]['judgement'] = 'failed'
                reason = "judge_failed"
                break
            if len(verdicts) != len(candidates) or any(
                    not isinstance(v, dict) or set(v) != {"relevant", "novel"}
                    or not all(isinstance(value, bool) for value in v.values()) for v in verdicts):
                raise ValueError("judge must return {relevant: bool, novel: bool} per candidate")
            with lock:
                for c, v in zip(candidates, verdicts):
                    provenance[str(c['id'])]['judgement'] = dict(v)
            fresh = [(c, v) for c, v in zip(candidates, verdicts) if v["relevant"] and v["novel"]]
            with lock:
                new += len(fresh)
                relevant.extend(c for c, _ in fresh)
            if not fresh:
                reason = "no_new_content"
                break
            if pages >= max_pages_per_query:
                reason = "max_pages_per_query"
                break
        traces[index] = {"query": query, "pages": pages, "read": read, "new": new, "stop": reason}

    with ThreadPoolExecutor(max_workers=min(8, len(queries))) as pool:
        list(pool.map(lambda pair: run(*pair), enumerate(queries)))
    # Claim order (under lock) is the first-discovery order, independent of thread completion.
    note, included = [], set()
    for candidate in sorted(relevant, key=lambda c: discovery[c["id"]]):
        if candidate["id"] not in included:
            note.append(candidate)
            included.add(candidate["id"])
            added[str(candidate["id"])] = provenance[str(candidate["id"])]
        if candidate.get("group_id") is not None:
            with pg.connect(dsn) as conn, conn.cursor() as cur:
                cur.execute("select * from knowledge.expand_group(%s,%s)",
                            (candidate["host_id"], candidate["group_id"]))
                for sibling in pg._rows(cur):
                    sibling["id"] = str(sibling["id"])
                    if sibling["id"] not in included:
                        note.append(sibling)
                        included.add(sibling["id"])
                        added[str(sibling["id"])] = {"stage": "group", "via": str(candidate["id"])}
    # "relevant" = 판정자가 고른 조각만(그룹 확장 전). 너겟 재현율 주 지표용.
    relevant_only = sorted({c["id"]: c for c in relevant}.values(), key=lambda c: discovery[c["id"]])
    if diagnostics is not None:
        diagnostics.update(added=added, trace=traces, seen=provenance)
    return {"note": note, "relevant": relevant_only, "trace": traces}
