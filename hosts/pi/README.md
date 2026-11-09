# hosts/pi

Pi model transport only. `writer_host.mjs` / `active_worker_host.mjs` load the installed Pi `ModelRuntime` and never create
agent sessions, extensions or tools; `writer_transport.py` / `active_worker_transport.py` call them from Python.
The core reaches these only at runtime by module name (`packages/core/src/host_defaults.py`) or through injected
`writer_factory` / `worker_factory`. No pi-fabric dependency here either.

Run: `python3 -m pytest -q hosts/pi/tests` (the Node replay test is skipped when Pi is not installed).
