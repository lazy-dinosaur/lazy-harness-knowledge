"""Explicit remote embedding profiles. Same dimension never means same vector space."""
import os

E5 = 'intfloat/multilingual-e5-large'
PPLX = 'perplexity/pplx-embed-v1-0.6b'
LOCAL = 'intfloat/multilingual-e5-small'
PROFILES = {E5:{'dimensions':1024,'format':'e5-role-prefix','usd_per_million_tokens':.02},
            PPLX:{'dimensions':1024,'format':'raw','usd_per_million_tokens':.004}}


class ModelMismatch(RuntimeError):
    pass


def remote_model():
    value = os.environ.get('LH_EMBED_MODEL', PPLX).strip()
    if value not in PROFILES:
        raise ModelMismatch('LH_EMBED_MODEL must select a supported 1024-dimensional remote model: ' + ', '.join(PROFILES))
    return value


def profile(model):
    if model not in PROFILES:
        raise ModelMismatch('unknown remote embedding model: ' + str(model))
    return {'model':model, 'model_id':model+'@openrouter', **PROFILES[model]}


def check_provider(provider):
    configured = os.environ.get('LH_EMBED_MODEL')
    if provider == 'local' and configured and configured not in (LOCAL,):
        raise ModelMismatch('local lh-embed is pinned e5-small; configured model cannot be served locally: ' + configured)
    if provider == 'openrouter':
        remote_model()


def format_inputs(model, texts, role):
    profile(model)
    if role not in ('query','passage') or not all(isinstance(t,str) for t in texts):
        raise ValueError('invalid embedding input role/text')
    return [role+': '+t for t in texts] if model == E5 else list(texts)
