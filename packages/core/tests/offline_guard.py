"""Process-wide offline guards for tests: no network, no subprocess, no real database driver. Import first."""
import os
import socket
import subprocess
import sys
import types
from pathlib import Path

# Install before production imports; no pytest/conftest or model services.
os.environ['LH_EMBED_PROVIDER']='local'
os.environ['LH_KNOWLEDGE_CONFIG']=str(Path(__file__).parent/'offline-config-does-not-exist.json')
def forbidden(*a,**k): raise AssertionError('offline side effect forbidden')
socket.create_connection=forbidden
socket.getaddrinfo=forbidden
socket.socket.connect=forbidden
socket.socket.connect_ex=forbidden
subprocess.run=forbidden
subprocess.Popen=forbidden
# Import-only DB driver stub: these standalone tests inject fake connections.
# Missing optional psycopg must never require installation or allow a real DB.
import sys
import types
class OfflineDriverError(Exception): pass
_offline_driver = types.ModuleType('psycopg')
_offline_driver.Error = OfflineDriverError
_offline_driver.connect = forbidden
sys.modules['psycopg'] = _offline_driver
