#!/usr/bin/env bash
# Each package runs in its own process: core tests install process-wide offline guards (no subprocess/network),
# while the Pi host test needs to start Node for its fake-wire replay.
set -euo pipefail
cd "$(dirname "$0")"
python3 -m pytest -q -p no:cacheprovider packages/core/tests
python3 -m pytest -q -p no:cacheprovider hosts/pi/tests
