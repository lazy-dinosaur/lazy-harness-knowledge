"""Offline-injectable worktime review of proposed knowledge entries."""
import argparse
import copy
import json
import os
from pathlib import Path

import digest_driver
import runner
import store_pg
import write_contract as wc
import validation_receipts as vr


import templates  # single source: migration seeds (knowledge.question_template)


def _questions():
    return templates.record_need()


def _intent_questions():
    return templates.intent()


def build_packet(fact, existing_excerpt):
    operation = fact.get("operation", "add")
    narratives = {"add": "원문 근거와 함께 추가 후보가 제출된 상태다.",
                  "update": "기존 기록에 대한 수정 후보가 원문 근거와 함께 제출된 상태다.",
                  "deprecate": "기존 기록에 대한 폐기 후보가 원문 근거와 함께 제출된 상태다."}
    if operation not in narratives:
        raise NotImplementedError(f"worktime operation {operation} has no supported wire template")
    if any(not isinstance(fact.get(k), str) for k in ("fact", "reason", "evidence_quote")) or ("why" in fact and not isinstance(fact["why"], str)):
        raise ValueError("worktime fact/reason/evidence_quote/why must be strings")
    refs = [r for r in fact.get("evidence_refs", []) if isinstance(r, dict) and r.get("quote")]
    # Jev sees every evidence ref (standard v1 criterion 5), not only the single summary quote.
    quote = ("\n".join(f"[{r.get('type')} {r.get('locator')}] {r['quote']}" for r in refs)
             if len(refs) > 1 else fact["evidence_quote"])
    # HOST owns the descriptive narrative. Writer explanations remain verbatim audit
    # DATA on the original fact; they are never projected as actor instructions.
    state = {"narrative": narratives[operation], "candidate_fact": fact["fact"], "evidence_quote": quote}
    if operation == "add":
        state["existing_records_excerpt"] = existing_excerpt
        return {"template_id": "record-need", "template_version": "v0.2.2",
                "questions": _questions(), "state": state}
    if operation == "update":
        state["target_excerpt"] = existing_excerpt
        return {"template_id": "intent", "template_version": "v0.3.1",
                "questions": _intent_questions(), "state": state}
    if operation == "deprecate":
        state["target_excerpt"] = existing_excerpt
        state["deprecate_reason"] = state.pop("candidate_fact")
        return {"template_id": templates.DEPRECATE[0], "template_version": templates.DEPRECATE[1],
                "questions": templates.deprecate(), "state": state}
    raise NotImplementedError(f"worktime operation {operation} has no supported wire template")


def pending_rewrites(facts):
    """{fragment_id: text after this work, or None if deprecated} for the update/deprecate facts of one work.

    2026-10-01 main canon split: adds split out of a fragment were judged duplicates of that fragment's old text,
    which the same work was rewriting (11 of 17 adds silently rejected). An add is compared with the canon as the
    work leaves it."""
    out = {}
    for f in facts:
        ref = f.get("target_ref")
        if ref and f.get("operation") == "update":
            out[str(ref)] = f.get("fact")
        elif ref and f.get("operation") == "deprecate":
            out[str(ref)] = None
    return out


def existing_excerpt(dsn, host, fact, pending=None, vector=None, mode=None):
    """vector: a precomputed query vector (no service call); mode 'text': the embedding service was down when the
    digestion judged, so the excerpt is rebuilt the text-fallback way (same prefix)."""
    if fact.get('_domain') is not None:
        import subject_dict
        with store_pg.connect(dsn) as conn, conn.cursor() as cur:
            hit = subject_dict.lookup(cur, host, fact.get('subject'), fact['_domain'])
            cur.execute('''select id::text,text from knowledge.fragment where host_id=%s and domain=%s
                and subject_id=%s and active order by updated_at desc limit 200''',
                (host, fact['_domain'], hit['subject_id'] if hit else None))
            hits = [{'id': r[0], 'text': r[1]} for r in cur.fetchall()]
    elif mode == "text":
        hits = [{**h, "warning": "embedding service unavailable; text fallback"}
                for h in store_pg.search(dsn, host, fact["fact"], limit=5, mode="text", expand=False)]
    else:
        hits = store_pg.search(dsn, host, fact["fact"], limit=5, mode="hybrid", expand=False, vector=vector)
    if pending:
        hits = [{**h, "text": pending[str(h.get("id"))]} if str(h.get("id")) in pending else h for h in hits]
        hits = [h for h in hits if h.get("text")]
        # a fragment this work rewrites into the add's subject was ranked on its old text: show it anyway
        subject = fact.get("subject") if isinstance(fact.get("subject"), str) else ""
        seen = {str(h.get("id")) for h in hits}
        hits += [{"id": fid, "text": text} for fid, text in pending.items()
                 if text and fid not in seen and subject.strip() and subject.strip() in text]
    fallback = any(hit.get("warning") for hit in hits)
    if not hits and mode == "text":
        fallback = True
    elif not hits and vector is None:
        # search returns no metadata on an empty result set; check the local encoder
        # so the caller still sees that a hybrid search fell back to text.
        import embed
        try:
            embed.encode_query(fact["fact"])
        except embed.EmbeddingUnavailable:
            fallback = True
    prefix = "[text fallback: embedding service unavailable] " if fallback else ""
    if not hits:
        return prefix + "이 host 의 정본에 관련 조각이 없다."
    return prefix + "\n".join(hit["text"][:600] for hit in hits)  # atomic facts are short; 240 cut conditions off


def _proposed(dsn, window, entry_ids=None):
    with store_pg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("""select entry_id::text,host_id,partition_key,work_unit_id::text,judgement_id,judgement_version,judgement_body from knowledge.ledger_entry
                    where state='proposed' and (%s::uuid[] is null or entry_id=any(%s::uuid[]))
                    order by created_at,entry_id limit %s""", (entry_ids, entry_ids, window))
        return store_pg._rows(cur)


def _target_excerpt(dsn, host, fact):
    with store_pg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("select text from knowledge.fragment where id=%s and host_id=%s and active",
                    (fact["target_ref"], host))
        row = store_pg._row(cur)
    if row is None:
        raise ValueError("target fragment missing in host")
    return row["text"]


def run_worktime(dsn, window, judge, *, utterance_judge=None, entry_ids=None, semantic_grounding=False):
    if window < 1:
        raise ValueError("window must be positive")
    fixtures, failures, utterances = {}, [], {}
    for entry in _proposed(dsn, window, entry_ids):
        body = entry['judgement_body']
        # Malformed legacy bodies must reach batch's durable rejection, not crash
        # warm/pending_rewrites or trigger any model call first.
        if wc.facts_shape(body.get('facts')) or any(wc.fact_shape(f, wc.Contract.PG_LEDGER_V1) for f in body['facts']):
            continue
        import embed
        from embedding_inputs import warm
        try:
            warm(entry['judgement_body'].get('facts', []), entry.get('partition_key', ''))
        except embed.EmbeddingUnavailable:
            pass
        prepared = []
        pending = pending_rewrites(entry["judgement_body"].get("facts", []))
        for index, fact in enumerate(entry["judgement_body"].get("facts", [])):
            packet, packet_errors = None, []
            try:
                excerpt = (_target_excerpt(dsn, entry["host_id"], fact)
                           if fact.get("operation") == "update" else
                           existing_excerpt(dsn, entry["host_id"], fact,
                                            pending if fact.get("operation", "add") == "add" else None))
                packet = build_packet(fact, excerpt)
                # Lint before any paid call: batch rejects the whole entry on a packet lint error,
                # so judging the other facts first would only waste Jev calls (cycle-02 finding).
                packet_errors = [e for e in runner.lint(packet)["errors"]
                                 if not (semantic_grounding and e["code"] == "E_CLAIM_QUOTE")]
                if packet_errors:
                    raise ValueError("packet lint: " + ", ".join(sorted({e["code"] for e in packet_errors})))
                judged = judge(copy.deepcopy(packet))
                if not isinstance(judged, dict) or not isinstance(judged.get("answers"), dict):
                    raise ValueError("judge must return {'answers': <typed answer map>, ...}")
                fixture = {"packet": packet, "answers": judged["answers"]}
                for key in ("jev_model_requested", "jev_model_actual", "input_tokens", "output_tokens",
                            "cost_usd", "usage_source"):
                    if key in judged:
                        fixture[key] = judged[key]
                if fact.get("evidence_source") == "user_confirmed" and utterance_judge:
                    status = utterance_judge(fact)
                    fixture["utterance_status"] = status
                    utterances.setdefault(entry["entry_id"], {})[index] = status
                prepared.append(fixture)
            except Exception as exc:
                # Exact lint reasons survive; external exceptions retain a typed class
                # without storing possibly credential-bearing driver/HTTP text.
                reasons = packet_errors or [wc.error('E_REVIEW_PREPARE', f'facts.{index}', type(exc).__name__)]
                receipt = wc.failure_receipt(body, reasons, stage='worktime_prepare', contract=wc.Contract.PG_LEDGER_V1,
                                             entry=entry, checked={'fact': fact, 'packet': packet}, fact_index=index)
                vr.save(dsn, receipt)  # fail loudly if the failure itself cannot be persisted
                failures.append({"entry_id": entry["entry_id"], "fact_index": index,
                                 "error": type(exc).__name__, 'errors': reasons,
                                 'validation_receipt_id': receipt['receipt_id'], 'request_id': receipt['request_id']})
                prepared = None
                break
        if prepared is not None:
            fixtures[entry["entry_id"]] = prepared
    # batch only sees fully judged entries; missing fixtures remain proposed.
    results = store_pg.batch(dsn, window, fixtures, entry_ids=entry_ids)
    for result in results:
        if result["entry_id"] in utterances and result["entry_id"] in fixtures:
            result["utterance_status"] = utterances[result["entry_id"]]
    return {"results": results, "failures": failures}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--window", type=int, required=True)
    parser.add_argument("--judge", choices=("jev", "fixture"), default="fixture")
    parser.add_argument("--fixture")
    parser.add_argument("--test-dsn", action="store_true", help="use LH_KNOWLEDGE_TEST_DSN instead of knowledge.json db_url")
    args = parser.parse_args()
    import config
    dsn = os.environ.get("LH_KNOWLEDGE_TEST_DSN") if args.test_dsn else config.load()["db_url"]
    if not dsn:
        parser.error("database not configured (knowledge.json db_url, or --test-dsn with LH_KNOWLEDGE_TEST_DSN)")
    if args.judge == "fixture":
        if not args.fixture:
            parser.error("fixture judge requires --fixture")
        response = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
        judge = lambda packet: response
    else:
        if args.fixture:
            parser.error("--fixture only applies to fixture judge")
        judge = digest_driver._jev_judge
    print(json.dumps(run_worktime(dsn, args.window, judge), ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
