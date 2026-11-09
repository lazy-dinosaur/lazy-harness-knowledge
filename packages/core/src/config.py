"""Single knowledge-module configuration: ~/.config/lazy-harness-v2/knowledge.json (mode 600).

v2 uses its own namespace (lazy-harness-v2) so it stays fully isolated from v1 during dogfooding.

Resolution per value: environment variable (development override) -> knowledge.json -> built-in default.
Secrets are never printed; errors name the missing key, not its value. See README "Configuration"."""
import json
import os
import stat
from pathlib import Path

DEFAULT_PATH = Path.home() / ".config/lazy-harness-v2/knowledge.json"
DEFAULT_MODEL_DIR = Path.home() / ".local/share/lazy-harness-v2/e5-small"
DEFAULT_STATE_DIR = Path.home() / ".local/state/lazy-harness-v2"
PATH_ENV = "LH_KNOWLEDGE_CONFIG"

# config key path -> (environment override, default)
FIELDS = {
    ("db_url",): ("LH_KNOWLEDGE_DB_URL", None),
    ("default_host",): ("LH_KNOWLEDGE_DEFAULT_HOST", None),
    ("jev", "api_key"): ("TYPESAFE_API_KEY", None),
    ("jev", "base_url"): ("TYPESAFE_BASE_URL", "https://openrouter.ai/api"),
    # OpenRouter latest alias (verified 2026-09-25: resolves to typesafe/jev-1.13-20260917; "typesafe/jev-latest" is 400)
    ("jev", "model"): ("LH_JEV_MODEL", "~typesafe/jev-latest"),
    ("embed_url",): ("LH_EMBED_URL", "http://127.0.0.1:8765"),
    # local = pinned e5-small HTTP service (tests, offline); openrouter = intfloat/multilingual-e5-large via OpenRouter
    ("embed", "provider"): ("LH_EMBED_PROVIDER", "local"),
    ("embed", "model_dir"): ("LH_EMBED_MODEL_DIR", str(DEFAULT_MODEL_DIR)),
}


class ConfigError(RuntimeError):
    pass


def path():
    return Path(os.environ.get(PATH_ENV) or DEFAULT_PATH)


def _file():
    p = path()
    if not p.exists():
        return {}
    if stat.S_IMODE(p.stat().st_mode) & (stat.S_IRWXG | stat.S_IRWXO):
        raise ConfigError(f"{p} must not be readable by group/others (chmod 600); refusing to read secrets")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{p} is not valid JSON (line {exc.lineno})") from None
    if not isinstance(data, dict):
        raise ConfigError(f"{p} must contain a JSON object")
    return data


def load():
    """Flat dict {db_url, jev_api_key, jev_base_url, jev_model, embed_url}; None when unset."""
    data, out = _file(), {}
    for keys, (env, default) in FIELDS.items():
        value = os.environ.get(env)
        if not value:
            node = data
            for k in keys:
                node = node.get(k) if isinstance(node, dict) else None
            value = node if isinstance(node, str) and node.strip() else None
        out["_".join(keys)] = value or default
    return out


def require(*names):
    """Load and fail listing missing names (never values)."""
    cfg = load()
    missing = [n for n in names if not cfg.get(n)]
    if missing:
        raise ConfigError(f"missing configuration: {', '.join(missing)} (set them in {path()} — see README)")
    return cfg


def source_authority(cfg, host_id, *, root_override=None, host_override=None, manifest_override=None):
    """Resolve one explicit per-host source authority; never infer a root from cwd or siblings.

    CLI root/host values are an atomic override pair. Configured authorities use the V2
    knowledge.json `source_authorities` namespace and are revalidated at the boundary.
    """
    from pathlib import Path

    if bool(root_override) != bool(host_override):
        raise ConfigError("source root and source host overrides must be supplied together")
    if root_override:
        if host_override != host_id:
            raise ConfigError("source authority host mismatch")
        root, manifest = root_override, manifest_override
    else:
        raw = _file().get("source_authorities", {})
        binding = raw.get(host_id) if isinstance(raw, dict) else None
        if not isinstance(binding, dict):
            return None
        if binding.get("host_id", host_id) != host_id:
            raise ConfigError("configured source authority host mismatch")
        root, manifest = binding.get("root"), binding.get("import_manifest")
        if manifest_override is not None:
            raise ConfigError("source manifest override requires explicit root and host overrides")
    if not isinstance(root, (str, Path)) or not str(root) or not Path(root).is_absolute():
        raise ConfigError("configured source authority requires an absolute root")
    try:
        resolved_root = Path(root).resolve(strict=True)
    except OSError:
        raise ConfigError("configured source authority root is unavailable") from None
    if not resolved_root.is_dir():
        raise ConfigError("configured source authority root is not a directory")
    if manifest is not None:
        if not isinstance(manifest, (str, Path)) or not str(manifest):
            raise ConfigError("configured source manifest is invalid")
        manifest_path = Path(manifest)
        if manifest_path.is_absolute():
            try:
                resolved_manifest = manifest_path.resolve(strict=True)
                resolved_manifest.relative_to(resolved_root)
            except (OSError, ValueError):
                raise ConfigError("configured source manifest must be inside its authorized root") from None
        else:
            manifest = manifest_path.as_posix()
    return {"host_id": host_id, "root": str(resolved_root), "import_manifest": manifest}


def apply_embed_env(cfg):
    # embed.py reads LH_EMBED_URL; export the resolved value so there is one source.
    if cfg.get("embed_url"):
        os.environ["LH_EMBED_URL"] = cfg["embed_url"]
