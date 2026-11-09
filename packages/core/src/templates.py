"""Canonical Jev question templates for the knowledge module (single source).
record-need v0.2.2 and intent v0.3.1 are also seeded by migrations/0001 (knowledge.question_template);
test_templates.py keeps both in lockstep. utterance_status is the acceptance-policy question
verified in jev-utterance-status-15 (active root evidence)."""
import json
import re
from pathlib import Path

_SQL = Path(__file__).resolve().parent.parent / "migrations/0001_knowledge_init.sql"


def _seed(template_id, version):
    match = re.search(r"\('" + re.escape(template_id) + r"','" + re.escape(version) + r"',\$json\$(.*?)\$json\$::jsonb\)",
                      _SQL.read_text(encoding="utf-8"), re.S)
    if not match:
        raise ValueError(f"{template_id} {version} seed missing")
    return json.loads(match.group(1))


RECORD_NEED = ("record-need", "v0.2.2")
INTENT = ("intent", "v0.3.1")


def record_need():
    return _seed(*RECORD_NEED)


def intent():
    return _seed(*INTENT)


UTTERANCE_STATUS = {"type": "choice", "instructions": "user_utterance 는 결정을 확정한 발언인가, 아직 검토 중인 의견·질문인가?", "criteria": {"confirmed_decision": "선택·지시·정정으로 결정을 확정한 발언이다", "tentative_opinion": "의견·추측·질문이며 결정을 확정하지 않은 발언이다", "none_or_uncertain": "판단할 수 없거나 불확실하다"}}


# G2 (2026-09-28): deprecate wire. Not seeded by a migration: _receipt inserts the template row on first use
# (question_template 'on conflict do nothing'), so the live DB needs no schema change. Values match runner.decide.
DEPRECATE = ("intent-deprecate", "v0.1")
DEPRECATE_QUESTIONS = {
    "invalidation_evidence": {"type": "choice",
        "instructions": "evidence_quote 와 deprecate_reason 이 target_excerpt 의 내용이 이제 틀렸거나 더 이상 쓰이지 않음을 직접 보여주는가?",
        "criteria": {"invalidates": "target_excerpt 의 내용이 이제 틀렸거나 더 이상 쓰이지 않음을 보여준다",
                     "consistent": "target_excerpt 의 내용은 여전히 맞다",
                     "insufficient": "판정하기에 부족하다",
                     "none_or_uncertain": "판단할 수 없거나 불확실하다"}},
    "replacement_exists": {"type": "choice",
        "instructions": "target_excerpt 를 지식에서 빼도 알아야 할 내용이 사라지지 않는가? (대신할 내용이 이미 있거나 함께 기록되었거나, 대상 자체가 없어져 대신할 내용이 필요 없는가)",
        "criteria": {"replacement_provided": "대신할 내용이 이미 있거나 이번에 함께 기록된다",
                     "no_replacement_needed": "대상이 없어져 대신할 내용이 필요 없다",
                     "replacement_missing": "대신할 내용이 필요한데 없다",
                     "none_or_uncertain": "판단할 수 없거나 불확실하다"}},
}


def deprecate():
    return DEPRECATE_QUESTIONS


def utterance_packet(preceding_context, user_utterance):
    return {"state": {"preceding_context": preceding_context, "user_utterance": user_utterance},
            "questions": {"utterance_status": UTTERANCE_STATUS}}
