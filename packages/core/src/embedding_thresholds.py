"""Model-bound cosine thresholds. Jev probability gates are NOT embedding thresholds."""
import hashlib
import json
import math
import os
from pathlib import Path

E5 = {'capture_dedupe':.935, 'rule_select':.83, 'retrieval':None}
REQUIRED = ()  # No cosine calibration prerequisite for absorption/retrieval.


class CalibrationRequired(RuntimeError):
    pass


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def read_profile(model_id, path=None):
    path = path or os.environ.get('LH_EMBED_THRESHOLDS')
    if not path:
        raise CalibrationRequired('model-specific calibration config required (LH_EMBED_THRESHOLDS)')
    try:
        value = json.loads(Path(path).read_text())
    except (OSError,ValueError) as exc:
        raise CalibrationRequired('missing/unreadable model calibration; run approved calibrate phase') from exc
    if value.get('status') != 'applied' or value.get('model_id') != model_id:
        raise CalibrationRequired('calibration not applied or embedding model mismatch')
    if value.get('sha256') != fingerprint({k:v for k,v in value.items() if k != 'sha256'}):
        raise CalibrationRequired('calibration config integrity mismatch')
    for name, number in value.get('thresholds',{}).items():
        if name not in E5 or (number is None and name != 'retrieval') or (number is not None and (type(number) not in (float,int) or not math.isfinite(number) or not -1 <= number <= 1)):
            raise CalibrationRequired('invalid calibrated threshold: '+name)
    if any(name not in value.get('thresholds',{}) for name in REQUIRED):
        raise CalibrationRequired('required absorption thresholds missing')
    return value


def get(name):
    import embed
    if name not in E5:
        raise CalibrationRequired('unknown cosine gate: '+name)
    if embed.MODEL_ID.startswith('intfloat/multilingual-e5-'):
        return E5[name]
    if name == 'retrieval':
        return None  # Ranking is model independent; no calibrated default floor.
    config = read_profile(embed.MODEL_ID)
    if name not in config['thresholds']:
        raise CalibrationRequired('no matching labeled pilot evidence for '+name+'; disabled, not inherited from e5')
    return config['thresholds'][name]
