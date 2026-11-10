"""Injected Jev page judge; no request is made until the returned judge is called."""
import json
import math
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from collect import JudgePageFailure

QUESTIONS = {
    "narrow": "candidates 의 {i}번 조각은 topic 이 가리키는 바로 그 기능·규칙·동작·구현에 대한 설명인가? 다른 기능의 비슷한 개념(예: 다른 곳의 중단 조건·저장소)이면 false.",
    "relaxed": "candidates 의 {i}번 조각은 topic 을 이해하거나 topic 의 기능을 작업할 때 알아야 할 내용인가?",
}
CRITERIA = {
    "narrow": {"true": "topic 이 가리키는 그 기능·규칙에 대한 설명이다", "false": "다른 기능이거나 주제와 무관하다"},
    "relaxed": {"true": "topic 을 다루는 데 필요한 내용이다", "false": "topic 과 무관하다"},
}


def safe_error(error, key):
    return type(error).__name__ + (" (credential redacted)" if key and key in str(error) else "")


def make_jev_judge(topic_title, queries, *, base_url, api_key, model="~typesafe/jev-latest",
                   question="relaxed", threshold=0.5, raw_dir=None, http=urlopen):
    """Return a collect-compatible judge with a .judgments list of successful scores.

    raw_dir, when supplied, receives redacted response bytes, including failed HTTP bodies.
    Failure details are not logged; all failed pages raise JudgePageFailure after two attempts.
    """
    if not isinstance(model, str) or not model.strip():
        raise ValueError("model is required")
    if question not in QUESTIONS or not isinstance(threshold, (float, int)) or isinstance(threshold, bool) or not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("invalid question or threshold")
    if not base_url or not api_key:
        raise ValueError("base_url and api_key required")
    judgments = []
    page = 0

    def judge(query, candidates):
        nonlocal page
        page += 1
        packet = {"state": {"topic": topic_title, "queries": queries,
                             "candidates": [{"i": i, "text": c["text"]} for i, c in enumerate(candidates)]},
                  "questions": {str(i): {"type": "noul", "instructions": QUESTIONS[question].format(i=i),
                                         "criteria": CRITERIA[question]} for i in range(len(candidates))},
                  "model": model}
        for attempt in (1, 2):
            try:
                request = Request(base_url.rstrip("/") + "/v1/systemone",
                                  data=json.dumps(packet, ensure_ascii=False).encode("utf-8"),
                                  headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
                                  method="POST")
                with http(request, timeout=60) as response:
                    raw = response.read()
                parsed = json.loads(raw)
                answers = parsed["answers"]
                if not isinstance(answers, dict) or set(answers) != set(packet["questions"]):
                    raise ValueError("answer keys mismatch")
                scores = [answers[str(i)]["noul"] for i in range(len(candidates))]
                if any(answers[str(i)].get("type") != "noul" or not isinstance(s, (int, float))
                       or isinstance(s, bool) or not math.isfinite(s) or not 0 <= s <= 1
                       for i, s in enumerate(scores)):
                    raise ValueError("invalid noul scores")
                if raw_dir is not None:
                    path = Path(raw_dir) / f"page{page:03d}-attempt{attempt}.json"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(raw.replace(api_key.encode(), b"[REDACTED]"))
                for candidate, score in zip(candidates, scores):
                    judgments.append({"query": query, "candidate_id": candidate["id"],
                                      "score": score, "relevant": score >= threshold})
                return [{"relevant": score >= threshold, "novel": score >= threshold} for score in scores]
            except (OSError, TimeoutError, ValueError, KeyError, TypeError) as error:
                if raw_dir is not None and isinstance(error, HTTPError):
                    path = Path(raw_dir) / f"page{page:03d}-attempt{attempt}.json"
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(error.read().replace(api_key.encode(), b"[REDACTED]"))
                # Do not expose exception text: HTTP errors can contain credentials.
                _ = safe_error(error, api_key)
        raise JudgePageFailure("Jev page failed after two attempts")

    judge.judgments = judgments
    return judge
