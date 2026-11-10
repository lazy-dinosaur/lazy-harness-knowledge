# hosts/jev

Direct Jev HTTP judgement transport. The core builds judgement requests and never performs network I/O for
judgement itself; it resolves `jev_http.send` by name (`packages/core/src/host_defaults.py`) unless the composition layer
installs its own transport with `host_defaults.install_judgement_http(send)` (for example lazy-harness through its
orchestration layer). Run: `python3 -m pytest -q hosts/jev/tests`.
