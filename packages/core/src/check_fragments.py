"""조각화 결과 자동 검사: 파싱·언어·개수 범위·줄 범위·그룹. 실패 기록 목록을 돌려준다."""
import json, math, re, sys

KANA = re.compile(r"[\u3040-\u30ff]")
HANGUL = re.compile(r"[가-힣]")
MIN_PER100 = 8  # prompt-v2 하한 기준, 검사 하한은 절반(100줄당 4)
MAX_PER100 = 45  # 실측 밀도 상한(안정성 시험 최대 44/100줄, 파일럿 02 R40 44) — prompt 권장 8~25 보다 넓게
MIN_HI = 12  # 작은 기록은 규칙 몇 개만으로도 비율이 커지므로 상한의 최소값
MAX_DROP_RATIO = 0.1  # 줄 범위가 틀린 조각은 그 조각만 버린다. 이 비율(또는 2개)을 넘으면 기록 실패

def repair_json(raw):
    """Luna 출력에서 관찰된 유일한 기계적 결함(끝 괄호 누락)만 복구한다. (복구본, 복구여부)."""
    try:
        json.loads(raw)
        return raw, False
    except Exception:
        for tail in ("}", "]}", "}]}"):
            try:
                json.loads(raw + tail)
                return raw + tail, True
            except Exception:
                pass
    return raw, False


def parse(raw):
    raw, _ = repair_json(raw)
    try:
        return json.loads(raw)
    except Exception:
        i = raw.find("{")
        if i < 0:
            return None
        try:
            return json.JSONDecoder().raw_decode(raw[i:])[0]
        except Exception:
            return None

def content_lines(text):
    return sum(1 for x in text.splitlines() if x.strip())

def check(record_id, source_text, raw):
    d = parse(raw)
    if not d or not d.get("records"):
        return {"record_id": record_id, "ok": False, "reasons": ["parse"], "n": 0}
    frags = [f for r in d["records"] if r.get("record_id") == record_id for f in r.get("fragments", [])]
    n, lines, reasons = len(frags), len(source_text.splitlines()), []
    c = content_lines(source_text)
    valid = [f for f in frags if isinstance(f.get("source_lines"), list) and len(f["source_lines"]) == 2
             and all(isinstance(x, int) and not isinstance(x, bool) for x in f["source_lines"])
             and 1 <= f["source_lines"][0] <= f["source_lines"][1] <= lines]
    dropped = n - len(valid)
    lo = max(1, int(c * MIN_PER100 / 100 * 0.5))
    hi = max(MIN_HI, math.ceil(c * MAX_PER100 / 100) + 1)
    if not lo <= len(valid) <= hi:
        reasons.append(f"count {len(valid)} not in [{lo},{hi}]")
    if any(KANA.search(f.get("text", "")) for f in frags):
        reasons.append("kana")
    if sum(1 for f in frags if not HANGUL.search(f.get("text", ""))) > 0:
        reasons.append("no_hangul")
    warnings = []
    if dropped:
        if dropped > max(2, int(n * MAX_DROP_RATIO)):
            reasons.append(f"source_lines {dropped}")
        else:
            warnings.append(f"dropped_source_lines {dropped}")
    if len(valid) > 3 and len({f.get("group") for f in valid}) == 1:
        reasons.append("single_group")
    return {"record_id": record_id, "ok": not reasons, "reasons": reasons, "warnings": warnings,
            "n": len(valid), "raw_n": n, "fragments": valid, "range": [lo, hi], "content_lines": c}

if __name__ == "__main__":
    rid, src, out = sys.argv[1:4]
    print(json.dumps(check(rid, open(src).read(), open(out).read()), ensure_ascii=False))
