# hosts/embed

Embedding transport: the local E5 service exchange (`send`, urlopen shape) and the remote provider exchange
(`remote_post`, async). The core builds requests and validates results; it resolves these by name
(`packages/core/src/host_defaults.py`) unless the composition layer installs its own with
`host_defaults.install_embedding_transport(local=..., remote=...)`. Run: `python3 -m pytest -q hosts/embed/tests`.
