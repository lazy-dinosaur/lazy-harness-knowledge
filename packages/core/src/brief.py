"""knowledge_brief: wide collection read by a cheap sub-agent (Luna) that returns only a four-view brief to the parent
(schema-delta '검색 전달 방식 확정', user 2026-09-28). The parent keeps reading source while this runs (async in the pi tool).

The wide document goes to a private temp file (mode 600) that the sub-agent reads with its read tool; the file is removed
as soon as the brief is written. The sub-agent is a headless `pi -p` run with only the read tool and no extensions.
"""
import os
import shutil
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import worker_tools

MODEL = os.environ.get("LH_BRIEF_MODEL", "openai/gpt-6-luna:medium")
TIMEOUT = int(os.environ.get("LH_BRIEF_TIMEOUT", "300"))
PROMPT = """너는 지식 정리 담당이다. 파일을 쓰거나 고치지 않는다. 최종 응답은 정리본(markdown)만.

작업 AI 가 지금 이 일을 하려 한다: {QUESTION}
{CHANGE}
아래 파일에 이 일과 관련해 모은 지식 조각이 네 칸(이전 결정·이유 / 현재 구현 / 유지해야 할 것 / 기타) × 영역으로 있다.
파일: {FILE}

파일을 끝까지 읽고(길면 여러 번 나눠 읽는다) 작업 AI 가 이 일을 하기 전에 알아야 할 것을 정리한다.
- 먼저 '영역 목록' 에서 이 일에 해당하는 영역을 고른다(조각 수가 많다고 주제 영역인 것은 아니다).
- **고른 영역의 조각은 하나도 빼지 않고 반영한다**(조건·값·순서·식별자·경로 그대로, 요약하며 세부를 줄이지 않는다).
- 다른 영역의 조각은 이 일과 **직접** 관련될 때만 쓴다.
- 조각에 없는 내용은 쓰지 않는다. 각 줄 끝에 근거 [alias] 를 붙인다.

절(이 순서로, 내용이 없는 절은 생략):
## 이전 결정·이유 (기각한 안 포함)
## 현재 구현 (계약·코드 경로·흐름·시나리오)
## 유지해야 할 것 (제약·보호 테스트·단일 진실원)
## 충돌 후보 ('⚠ 충돌 후보' 표시 조각과, 하려는 변경과 부딪히는 내용)
## 기타
"""


LIMIT_RULE = ("- **정리본은 8,000자 안으로 쓴다.** 넘칠 것 같으면 '이전 결정·이유', '유지해야 할 것', '충돌 후보' 를 먼저 빠짐없이 쓰고,\n"
              "  '현재 구현'·'기타' 는 한 줄 요약 뒤 근거 [alias] 목록으로 줄인다(작업 AI 가 필요하면 knowledge_more 로 원문을 받는다).\n")


VERBATIM_RULE = "- 조각 문장의 조건·값·순서·동작(무엇을 제한·제외·비우는지 등)을 가능한 한 원문 그대로 옮기고, 여러 조각을 한 문장으로 뭉뚱그리지 않는다.\n"


SPLIT_RULE = ("- 이름·설명이 비슷한 기능(화면·API·컴포넌트)이 여럿이면 기능별로 나눠 각각 빠짐없이 정리하고, "
              "지금 하려는 일이 가리키는 기능을 먼저 쓴다(이름이 비슷한 다른 기능은 따로 적는다).\n")


def _prompt():
    """8,000-char rule on by default (scale-02 stability, 2026-09-28: same-setting runs vary 80.4~90.7%; the cap
    averaged 82.4% vs 84.5% without it — inside the range, -2.1%p — with ~30% smaller briefs). LH_BRIEF_LIMIT=0 turns it off."""
    p = PROMPT
    if os.environ.get("LH_BRIEF_SPLIT") == "1":  # scale-02 split experiment switch (R07: similar features mixed up)
        p = p.replace("\n\n절(이 순서로", "\n" + SPLIT_RULE + "\n절(이 순서로", 1)
    if os.environ.get("LH_BRIEF_VERBATIM") == "1":  # scale-02 diag experiment switch (R07: collected but dropped by the writer)
        p = p.replace("\n\n절(이 순서로", "\n" + VERBATIM_RULE + "\n절(이 순서로", 1)
    if os.environ.get("LH_BRIEF_LIMIT", "1") != "0":
        p = p.replace("\n\n절(이 순서로", "\n" + LIMIT_RULE + "\n절(이 순서로", 1)
    return p


def _usage_from_message(msg):
    usage = msg.get("usage") if isinstance(msg, dict) and isinstance(msg.get("usage"), dict) else None
    if not usage:
        return None
    out = {"source": "pi-json-turn_end"}
    for src, dst in (("input_tokens", "input_tokens"), ("prompt_tokens", "input_tokens"),
                     ("output_tokens", "output_tokens"), ("completion_tokens", "output_tokens"),
                     ("total_tokens", "total_tokens")):
        if isinstance(usage.get(src), int) and dst not in out:
            out[dst] = usage[src]
    cost = usage.get("cost")
    if isinstance(cost, dict):
        value = cost.get("total")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out["cost_usd"] = float(value)
    elif isinstance(cost, (int, float)) and not isinstance(cost, bool):
        out["cost_usd"] = float(cost)
    # Preserve provider-specific metadata without inventing missing fields.
    out["raw_keys"] = sorted(usage.keys())
    return out


def pi_runner(prompt, cwd):
    """Headless sub-agent using the configured Pi profile.

    Returns ``(final_text, usage_meta)``. Older callers that only need text still use index 0;
    if the provider/CLI omits usage, usage_meta explicitly reports unknown rather than zero.
    """
    import json as _json
    exe = shutil.which("pi") or "pi"
    model = os.environ.get("LH_BRIEF_MODEL", MODEL)
    r = subprocess.run([exe, "-p", "--mode", "json", "--model", model, "--tools", "read", "--no-extensions", "--no-skills",
                        "--no-session", prompt], cwd=cwd, capture_output=True, text=True, timeout=TIMEOUT)
    text, usage_meta = "", {"usageKnown": False, "usage": "unknown", "profile": model}
    for line in r.stdout.splitlines():
        try:
            ev = _json.loads(line)
        except ValueError:
            continue
        if ev.get("type") == "turn_end":
            msg = ev.get("message") or {}
            parsed = _usage_from_message(msg)
            if parsed:
                # Sum every turn: a headless run reads the file over several turns and each turn_end carries only
                # that turn's usage (G14 cost diagnosis 2026-10-09: last-turn-only under-reported 1.2~5x).
                prev = usage_meta.get("usage") if usage_meta.get("usageKnown") else None
                if prev and isinstance(prev.get("cost_usd"), float) and isinstance(parsed.get("cost_usd"), float):
                    parsed = {**parsed, "cost_usd": prev["cost_usd"] + parsed["cost_usd"], "turns": prev.get("turns", 1) + 1}
                else:
                    parsed = {**parsed, "turns": 1}
                usage_meta = {"usageKnown": True, "usage": parsed, "profile": model}
            parts = [c.get("text", "") for c in msg.get("content") or [] if c.get("type") == "text"]
            if parts:
                text = "\n".join(parts).strip()
    if r.returncode != 0 or not text:
        raise RuntimeError("brief writer failed: " + (r.stderr or "empty output")[-300:])
    return text, usage_meta


MAP_PROMPT = PROMPT + """
MAP 단계: 이 파일은 전체 수집 문서의 일부다. 이 부분에서 작업 질문과 관련된 내용을 전부 추출한다.
작업 대상 영역의 모든 조각을 위 다섯 절에 빠짐없이 옮긴다. 관련 없는 영역은 아무것도 쓰지 않는다.
조건·값·순서·식별자·경로는 원문 그대로, 각 줄 끝의 [alias]도 유지한다.
이 단계에는 길이 상한이 없다. 줄을 줄이거나 합쳐 세부를 생략하지 않는다.
"""
REDUCE_RULE = """
REDUCE 단계: 파일은 모든 MAP 부분 정리본을 순서대로 합친 것이다. 끝까지 읽는다.
작업이 가리키는 영역의 줄은 하나도 누락하지 않고 최종 정리본에 반영한다.
이름·설명이 비슷하지만 다른 기능은 합치지 말고 기능별로 분리한다.
"""


def split_by_area(document, limit=30000):
    """Pack whole areas across views; split oversized areas only between intact lines.

    The repeated preamble and view/area headings count toward the limit. If even
    one line plus its context cannot fit, fail explicitly rather than truncate it.
    Short documents are returned byte-for-byte, without parsing or reordering.
    """
    if limit <= 0:
        raise ValueError("LH_BRIEF_PART_CHARS must be positive")
    if len(document) <= limit:
        return [document]
    header, areas = [], {}
    view = area = None
    for line in document.splitlines(keepends=True):
        if line.startswith("## "):
            view, area = line, None
        elif line.startswith("### 영역 ") and view is not None:
            area = line
            areas.setdefault(area, [])
        elif view is None:
            header.append(line)
        elif line.strip():
            if area is None:
                raise ValueError("collection line outside an area")
            areas[area].append((view, line))
    preamble = "".join(header)
    if not areas or not any(line.startswith("영역 목록: ") for line in header):
        raise ValueError("collection missing area structure")

    def render(area, rows):
        result, previous = [], None
        for view, line in rows:
            if view != previous:
                result.extend((view, "\n", area))
                previous = view
            result.append(line)
        return "".join(result) + "\n"

    blocks = []
    for area, rows in areas.items():
        whole = render(area, rows)
        if len(preamble) + len(whole) <= limit:
            blocks.append(whole)
            continue
        chunk = []
        for row in rows:
            if chunk and len(preamble) + len(render(area, chunk + [row])) > limit:
                blocks.append(render(area, chunk))
                chunk = []
            if len(preamble) + len(render(area, [row])) > limit:
                raise ValueError("fragment line and repeated headers exceed LH_BRIEF_PART_CHARS")
            chunk.append(row)
        if chunk:
            blocks.append(render(area, chunk))
    parts, current = [], preamble
    for block in blocks:
        if current != preamble and len(current) + len(block) > limit:
            parts.append(current)
            current = preamble
        current += block
    if current != preamble:
        parts.append(current)
    return parts or [document]


def _writer_result(got):
    text, cost = got if isinstance(got, tuple) else (got, None)
    if isinstance(cost, dict):
        usage = cost.get("usage") if isinstance(cost.get("usage"), dict) else {}
        cost = usage.get("cost_usd") if isinstance(usage.get("cost_usd"), (int, float)) else None
    return text, cost


def _sum_costs(costs):
    return None if any(cost is None for cost in costs) else sum(costs)


def _map_reduce(parts, directory, question, change, runner):
    parallel = int(os.environ.get("LH_BRIEF_PARALLEL", "4"))
    if parallel <= 0:
        raise ValueError("LH_BRIEF_PARALLEL must be positive")

    def write_private(path, text):
        # Create private, rather than chmod after exposing the content.
        with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as stream:
            stream.write(text)

    def call(template, path):
        prompt = template.replace("{QUESTION}", question.strip()).replace("{FILE}", str(path))
        prompt = prompt.replace("{CHANGE}", f"하려는 변경: {change.strip()}" if change else "")
        return _writer_result(runner(prompt, str(directory)))

    def one(item):
        index, part = item
        path = directory / f"part-{index + 1}.md"
        write_private(path, part)
        return call(MAP_PROMPT, path)

    # Executor shutdown waits for all readers before the caller's finally cleans up.
    # Any failed map propagates: reduction must never silently omit a part.
    with ThreadPoolExecutor(max_workers=parallel) as executor:
        notes = list(executor.map(one, enumerate(parts)))
    merged = directory / "partials.md"
    write_private(merged, "\n\n".join(f"# 부분 정리 {i + 1}\n{text}" for i, (text, _) in enumerate(notes)))
    text, reduce_cost = call(_prompt() + REDUCE_RULE, merged)
    map_cost = _sum_costs([cost for _, cost in notes])
    return text, _sum_costs([map_cost, reduce_cost]), map_cost, reduce_cost

def brief(dsn, host, question, queries, ask, change=None, runner=pi_runner):
    t0 = time.time()
    doc, stats = worker_tools.collect_wide(dsn, host, question, queries, ask, change=change)
    t1 = time.time()
    mapreduce = os.environ.get("LH_BRIEF_MAPREDUCE") == "1"
    parts = split_by_area(doc, int(os.environ.get("LH_BRIEF_PART_CHARS", "30000"))) if mapreduce else [doc]
    map_cost = reduce_cost = 0.0
    d = Path(tempfile.mkdtemp(prefix="lh-brief-"))
    try:
        os.chmod(d, 0o700)
        f = d / "knowledge.md"
        f.write_text(doc)
        os.chmod(f, 0o600)
        if len(parts) > 1:
            text, cost, map_cost, reduce_cost = _map_reduce(parts, d, question, change, runner)
        else:
            got = runner(_prompt().replace("{QUESTION}", question.strip()).replace("{FILE}", str(f))
                         .replace("{CHANGE}", f"하려는 변경: {change.strip()}" if change else ""), str(d))
            text, cost = _writer_result(got)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    result = {"brief": text, **stats, "collected_chars": len(doc), "brief_chars": len(text), "writer_cost_usd": cost,
              "collect_sec": round(t1 - t0, 1), "write_sec": round(time.time() - t1, 1)}
    if mapreduce:
        result.update(map_parts=len(parts), map_cost_usd=map_cost, reduce_cost_usd=reduce_cost)
    return result
