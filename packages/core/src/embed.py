"""Model-bound embeddings: remote pplx-0.6b (LH_EMBED_MODEL override), or pinned local e5-small.
Cache keys and stored vectors carry MODEL_ID; switching a host requires explicit reembedding.
The local ONNX path cannot serve a configured remote model.
"""
import hashlib
import json
import os
import urllib.error
import urllib.request
from embedding_cache import VectorCache

CACHE = VectorCache(os.environ.get("LH_EMBED_CACHE_DIR"))
from pathlib import Path

LOCAL_MODEL_ID = "intfloat/multilingual-e5-small@614241f622f53c4eeff9890bdc4f31cfecc418b3"
import embedding_models as models
REMOTE_MODEL = models.PPLX  # overwritten after provider resolution below
REMOTE_MODEL_ID = REMOTE_MODEL + "@openrouter"


def _provider():
    value = os.environ.get("LH_EMBED_PROVIDER")
    if not value:
        try:
            import config
            value = config.load().get("embed_provider")
        except Exception:  # noqa: BLE001
            value = None
    value = (value or "local").strip().lower()
    if value not in ("local", "openrouter"):
        raise RuntimeError("embed.provider must be local|openrouter")
    return value


PROVIDER = _provider()
models.check_provider(PROVIDER)
REMOTE_MODEL = models.remote_model() if PROVIDER == "openrouter" else models.PPLX
REMOTE_MODEL_ID = REMOTE_MODEL + "@openrouter"
MODEL_ID = REMOTE_MODEL_ID if PROVIDER == "openrouter" else LOCAL_MODEL_ID
# Model + venv location comes from knowledge.json embed.model_dir (default ~/.local/share/lazy-harness-v2/e5-small).
def _model_dir():
    try:
        import config
        return Path(config.load()["embed_model_dir"])
    except Exception:  # config errors must not hide the pinned-hash check below
        return Path(os.environ.get("LH_EMBED_MODEL_DIR") or Path.home() / ".local/share/lazy-harness-v2/e5-small")


_MODEL_DIR = _model_dir()
_PINS = {"model.onnx": "ca456c06b3a9505ddfd9131408916dd79290368331e7d76bb621f1cba6bc8665",
         "tokenizer.json": "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39"}


def _verify():
    models.check_provider("local")
    for name, expected in _PINS.items():
        digest = hashlib.sha256()
        with (_MODEL_DIR / name).open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != expected:
            raise RuntimeError(f"E5 {name} SHA256 mismatch")


def _infer(texts, role):
    import numpy as np
    import onnxruntime as ort
    from tokenizers import Tokenizer

    _verify()
    tokenizer = Tokenizer.from_file(str(_MODEL_DIR / "tokenizer.json"))
    tokenizer.enable_padding(pad_id=1, pad_token="<pad>")
    encodings = tokenizer.encode_batch([role + ": " + text for text in texts])
    if any(len(item.ids) > 512 for item in encodings):
        raise ValueError("E5 input exceeds 512 tokens; truncation forbidden")
    arrays = {"input_ids": np.asarray([e.ids for e in encodings], dtype=np.int64),
              "attention_mask": np.asarray([e.attention_mask for e in encodings], dtype=np.int64),
              "token_type_ids": np.asarray([e.type_ids for e in encodings], dtype=np.int64)}
    options = ort.SessionOptions()
    options.intra_op_num_threads = 2
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    session = ort.InferenceSession(str(_MODEL_DIR / "model.onnx"), sess_options=options,
                                   providers=["CPUExecutionProvider"])
    hidden = session.run(None, {item.name: arrays[item.name] for item in session.get_inputs()})[0]
    if hidden.ndim != 3 or hidden.shape[2] != 384:
        raise RuntimeError("unexpected E5 output dimensions")
    mask = arrays["attention_mask"][..., None].astype(np.float32)
    pooled = (hidden * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1)
    vectors = pooled / np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12)
    if not np.isfinite(vectors).all() or not np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-5):
        raise RuntimeError("invalid E5 vector")
    return vectors.tolist()


class EmbeddingUnavailable(RuntimeError):
    pass


def _remote(texts, role):
    import config
    import embedding_transport as transport
    cfg = config.require("jev_api_key", "jev_base_url")
    try:
        return transport.encode(texts, role, cfg['jev_base_url'].rstrip('/') + '/v1/embeddings',
                                {'Authorization': 'Bearer ' + cfg['jev_api_key']}, REMOTE_MODEL,
                                on_batch=lambda chunk, vectors: CACHE.put_many(MODEL_ID, role, chunk, vectors))
    except Exception as exc:
        if transport.retryable(exc):
            raise EmbeddingUnavailable('remote embedding unavailable after bounded retries') from exc
        raise


def _encode(texts, role):
    models.check_provider(PROVIDER)
    if not texts:
        return []
    if not all(isinstance(text, str) for text in texts):
        raise ValueError("E5 inputs must be strings")
    if PROVIDER == "openrouter":
        return _remote(texts, role)
    request = urllib.request.Request(
        os.environ.get("LH_EMBED_URL", "http://127.0.0.1:8765").rstrip("/") + "/embed",
        data=json.dumps({"texts": texts, "role": role}).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        import host_defaults
        with host_defaults.embedding_http()(request, timeout=30) as response:
            result = json.load(response)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        if isinstance(error, urllib.error.HTTPError):
            raise ValueError(error.read().decode("utf-8")) from error
        raise EmbeddingUnavailable("local embedding service unavailable") from error
    if result.get("model_id") != MODEL_ID:
        raise RuntimeError("embedding model_id mismatch")
    return result["vectors"]


def encode_model(texts, role, model):
    """Explicit remote-only evaluation encoding; does not change the active DB vector space."""
    import config
    import embedding_transport as transport
    models.profile(model)
    texts = list(texts)
    model_id = model + '@openrouter'
    with CACHE.lock:
        unique = list(dict.fromkeys(texts))
        missing = [t for t in unique if CACHE.get(model_id, role, t) is None]
        if missing:
            cfg = config.require('jev_api_key','jev_base_url')
            vectors = transport.encode(missing, role, cfg['jev_base_url'].rstrip('/')+'/v1/embeddings',
                {'Authorization':'Bearer '+cfg['jev_api_key']}, model,
                on_batch=lambda ts,vs:CACHE.put_many(model_id,role,ts,vs))
            CACHE.put_many(model_id,role,missing,vectors)
        return [list(CACHE.get(model_id,role,t)) for t in texts]

def configure_cache(directory=None):
    """Set an isolated persistent cache (pilot state); normal callers may use LH_EMBED_CACHE_DIR."""
    global CACHE
    CACHE = VectorCache(directory)


def _cached_encode(texts, role):
    models.check_provider(PROVIDER)
    texts = list(texts)
    if not all(isinstance(text, str) for text in texts):
        raise ValueError('E5 inputs must be strings')
    # Coalesce concurrent callers too; remote transport owns bounded batch parallelism.
    with CACHE.lock:
        unique = list(dict.fromkeys(texts))
        found = {text:CACHE.get(MODEL_ID, role, text) for text in unique}
        missing = [text for text in unique if found[text] is None]
        CACHE.hits += sum(found[text] is not None for text in texts)
        CACHE.misses += len(missing)
        if missing:
            if PROVIDER == 'openrouter':
                vectors = _encode(missing, role)
            else:
                from embedding_transport import BATCH_SIZE
                vectors = [v for start in range(0, len(missing), BATCH_SIZE)
                           for v in _encode(missing[start:start + BATCH_SIZE], role)]
            CACHE.put_many(MODEL_ID, role, missing, vectors)
            found.update(zip(missing, vectors))
        return [list(found[text]) for text in texts]


BROKER = None
BROKER_MODEL = None
BROKER_DIMENSION = None

def configure_broker(encoder=None, *, model_id=None, dimension=None):
    """Explicit public transport port. It owns quoting/cache; defaults never opt in."""
    global BROKER, BROKER_MODEL, BROKER_DIMENSION
    if encoder is None:
        if model_id is not None or dimension is not None:raise ValueError('broker reset has no model/dimension')
    elif not callable(encoder) or model_id != MODEL_ID or type(dimension) is not int or dimension < 1:
        raise ValueError('explicit model-bound embedding broker required')
    BROKER, BROKER_MODEL, BROKER_DIMENSION = encoder, model_id, dimension

def _brokered_encode(texts, role):
    if BROKER is None:return _cached_encode(texts,role)
    import math
    if BROKER_MODEL != MODEL_ID or role not in ('query','passage'):raise ValueError('embedding broker model/role drift')
    values=list(texts)
    if any(type(t) is not str for t in values):raise ValueError('embedding broker text required')
    vectors=BROKER(values,role)
    if (type(vectors) is not list or len(vectors)!=len(values) or
        any(type(v) is not list or len(v)!=BROKER_DIMENSION or
            any(type(x) not in (int,float) or not math.isfinite(x) for x in v) for v in vectors)):
        raise ValueError('embedding broker result shape')
    return [list(v) for v in vectors]

def encode_passages(texts):
    return _brokered_encode(texts, 'passage')


def encode_query(text):
    return _brokered_encode([text], 'query')[0]


def encode_queries(texts):
    return _brokered_encode(texts, 'query')


# Compatibility for callers that used the former lru_cache test/reset hook.
encode_query.cache_clear = lambda: CACHE.memory.clear()


