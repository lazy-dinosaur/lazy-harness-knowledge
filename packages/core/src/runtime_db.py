"""Immutable, offline-validated binding for one runtime PostgreSQL target.

This module parses connection options only; it never resolves hosts or opens sockets.
"""
from dataclasses import dataclass
from urllib.parse import parse_qsl, unquote, urlsplit


class RuntimeDBBindingError(ValueError):
    pass


_ROUTING_ENV = ("PGHOST", "PGHOSTADDR", "PGPORT", "PGDATABASE", "PGUSER", "PGSERVICE", "PGSERVICEFILE")


def _reject_environment_routing():
    import os
    if any(os.environ.get(name) for name in _ROUTING_ENV):
        raise RuntimeDBBindingError("environment database routing is not supported")


def _endpoint(dsn):
    if not isinstance(dsn, str) or not dsn.strip():
        raise RuntimeDBBindingError("runtime database binding is required")
    text = dsn.strip()
    import os
    _reject_environment_routing()
    if text.startswith(("postgres://", "postgresql://")):
        try:
            parsed = urlsplit(text)
        except ValueError:
            raise RuntimeDBBindingError("database URL is invalid") from None
        query_items = parse_qsl(parsed.query, keep_blank_values=True)
        query = {key.lower(): value for key, value in query_items}
        if any(key in query for key in ("hostaddr", "service")):
            raise RuntimeDBBindingError("routed database URL options are not supported")
        url_host = query.get("host") or parsed.hostname
        if not url_host:
            raise RuntimeDBBindingError("database URL must select an explicit host")
        if "," in url_host:
            raise RuntimeDBBindingError("multi-host database URLs are not supported")
        try:
            raw_port = parsed.port or query.get("port")
            if raw_port is None:
                raise RuntimeDBBindingError("database URL must select an explicit port")
            port = int(raw_port)
        except ValueError:
            raise RuntimeDBBindingError("runtime database port is invalid") from None
        database, user = unquote(parsed.path.lstrip("/")), unquote(parsed.username or "")
        if not database or not user:
            raise RuntimeDBBindingError("database URL must select an explicit database and user")
        if url_host.startswith("/"):
            return ("unix", url_host, port, database, user)
        return (url_host.lower(), port, database, user)
    try:
        from psycopg.conninfo import conninfo_to_dict
        fields = conninfo_to_dict(text)
    except Exception:
        try:
            from psycopg2.extensions import parse_dsn
            fields = parse_dsn(text)
        except Exception:
            raise RuntimeDBBindingError("database connection string is invalid") from None
    if fields.get("service") or fields.get("hostaddr"):
        raise RuntimeDBBindingError("service and hostaddr routing are not supported")
    raw_host = fields.get("host")
    if not raw_host:
        raise RuntimeDBBindingError("database connection string must select an explicit host")
    hosts = raw_host.split(",")
    if len(hosts) != 1 or not hosts[0]:
        raise RuntimeDBBindingError("multi-host database connection strings are not supported")
    try:
        if fields.get("port") is None or not fields.get("dbname") or not fields.get("user"):
            raise RuntimeDBBindingError("database connection string must select explicit port, database, and user")
        port = int(fields["port"])
    except ValueError:
        raise RuntimeDBBindingError("runtime database port is invalid") from None
    if hosts[0].startswith("/"):
        return ("unix", hosts[0], port, fields["dbname"], fields["user"])
    return (hosts[0].lower(), port, fields["dbname"], fields["user"])


@dataclass(frozen=True, repr=False)
class RuntimeDBContext:
    dsn: str
    endpoint: tuple
    provenance: str

    def __post_init__(self):
        actual = _endpoint(self.dsn)
        if actual != self.endpoint:
            raise RuntimeDBBindingError("runtime database context endpoint does not match its DSN")

    @classmethod
    def bind(cls, dsn, *, provenance="explicit-runtime"):
        if provenance not in {"explicit-runtime", "explicit-cli", "configured-entry", "explicit-diagnostic", "explicit-test"}:
            raise RuntimeDBBindingError("runtime database provenance label is invalid")
        endpoint = _endpoint(dsn)
        return cls(dsn=dsn.strip(), endpoint=endpoint, provenance=provenance)

    def __repr__(self):
        return f"RuntimeDBContext(provenance={self.provenance!r})"

    def assert_environment(self):
        _reject_environment_routing()

    def require_same_target(self, dsn):
        self.assert_environment()
        if _endpoint(dsn) != self.endpoint:
            raise RuntimeDBBindingError("runtime database target mismatch")
        return self.dsn


def bind_runtime_db(dsn, *, provenance="explicit-runtime"):
    if isinstance(dsn, RuntimeDBContext):
        return dsn
    return RuntimeDBContext.bind(dsn, provenance=provenance)
