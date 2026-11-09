"""Model-independent vector candidate budget; semantic decisions belong to Jev."""
import os


def top_k(value=None):
    raw = os.environ.get('LH_CANDIDATE_TOP_K', '5') if value is None else value
    if isinstance(raw, bool) or not str(raw).isdigit() or int(raw) < 1:
        raise ValueError('LH_CANDIDATE_TOP_K must be a positive integer')
    return int(raw)


def policy():
    return {'method': 'vector-top-k-then-jev', 'top_k': top_k(), 'cosine_cutoff': None,
            'none': True, 'probability_thresholds': 'unchanged'}
