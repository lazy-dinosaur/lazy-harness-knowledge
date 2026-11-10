"""Host-owned Pi model transport, not a Pi agent. Effects only inside writer call."""
import json
import math
import os
import re
from pathlib import Path
import shutil
import subprocess
from writer_worker import BINDING, MODEL, WriterFailure, build_request, parse_output
from safety import save


def _managed_runtime(launcher):
    """Resolve only the selected launcher's official active release; never run it."""
    if launcher.name != 'pi' or launcher.parent.name != 'bin':
        return None
    install = launcher.parent.parent / 'install'
    marker = install / 'managed-install.json'
    current = install / 'current-version'
    if not (marker.exists() or marker.is_symlink() or current.exists() or current.is_symlink()):
        return None
    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('duplicate managed metadata field')
            result[key] = value
        return result

    try:
        # Metadata and release links must not redirect discovery outside this root.
        if any(p.resolve() != p for p in (install, marker, current)):
            raise ValueError('managed metadata escape')
        metadata = json.loads(marker.read_text(encoding='utf-8'), object_pairs_hook=unique_fields)
        if (not isinstance(metadata, dict) or metadata.get('kind') != 'pi-managed-install' or
                type(metadata.get('schemaVersion')) is not int or metadata['schemaVersion'] != 1 or
                metadata.get('layout') != 'releases-v1'):
            raise ValueError('managed marker')
        entry = metadata.get('entrypoint')
        if (not isinstance(entry, dict) or entry.get('type') != 'symlink' or
                not isinstance(entry.get('path'), str) or not Path(entry['path']).is_absolute() or
                not Path(entry['path']).is_symlink() or Path(entry['path']).resolve() != launcher):
            raise ValueError('managed entrypoint')
        # Same single-line token alphabet as the official shell launcher; not a path.
        version = current.read_bytes().decode('utf-8').removesuffix('\n')
        if version in ('.', '..') or not re.fullmatch(r'[0-9A-Za-z._+-]+', version):
            raise ValueError('managed version')
        release = install / 'releases' / version
        sdk = release / 'node_modules/@earendil-works/pi-coding-agent'
        manifest = sdk / 'package.json'
        runtime = sdk / 'dist/core/model-runtime.js'
        cli = sdk / 'dist/bundle/cli.js'
        binary = release / 'node_modules/.bin/pi'
        if (any(p.resolve() != p for p in (release, sdk, manifest, runtime, cli)) or
                not runtime.is_file() or not cli.is_file() or not os.access(binary, os.X_OK) or
                binary.resolve() != cli):
            raise ValueError('managed release layout')
        package = json.loads(manifest.read_text(encoding='utf-8'), object_pairs_hook=unique_fields)
        if (not isinstance(package, dict) or package.get('name') != '@earendil-works/pi-coding-agent' or
                package.get('version') != version or package.get('type') != 'module'):
            raise ValueError('managed SDK module')
        return runtime
    except (OSError, ValueError, TypeError, RuntimeError):
        raise WriterFailure('E_WRITER_RUNTIME', 'installed Pi model runtime entrypoint unavailable') from None


def runtime_path():
    executable = shutil.which('pi')
    if not executable:
        raise WriterFailure('E_WRITER_RUNTIME', 'existing Pi runtime unavailable')
    try:
        launcher = Path(executable).resolve()
        managed = _managed_runtime(launcher)
        if managed is not None:
            return managed
        for parent in launcher.parents:
            candidate = parent / 'dist/core/model-runtime.js'
            if candidate.is_file(): return candidate
    except (OSError, ValueError, RuntimeError):
        raise WriterFailure('E_WRITER_RUNTIME', 'installed Pi model runtime entrypoint unavailable') from None
    raise WriterFailure('E_WRITER_RUNTIME', 'installed Pi model runtime entrypoint unavailable')


def host_call(mode, request=None, *, runtime=None, host_module="writer_host.mjs"):
    sdk = runtime or runtime_path()
    env = {k: os.environ[k] for k in ('PATH', 'HOME', 'PI_CODING_AGENT_DIR', 'OPENAI_API_KEY', 'SSL_CERT_FILE', 'SSL_CERT_DIR') if k in os.environ}
    env['PI_OFFLINE'] = '1'
    try:
        result = subprocess.run(['node', str(Path(__file__).with_name(host_module)), str(sdk), mode],
                                input=json.dumps(request, ensure_ascii=False, allow_nan=False),
                                capture_output=True, text=True, timeout=900, env=env)
    except subprocess.TimeoutExpired:
        raise WriterFailure('E_WRITER_TIMEOUT') from None
    except OSError:
        raise WriterFailure('E_WRITER_RUNTIME') from None
    try:
        output = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise WriterFailure('E_WRITER_TRANSPORT', 'invalid host response') from None
    if result.returncode or not isinstance(output, dict):
        raise WriterFailure('E_WRITER_TRANSPORT')
    return output


def make_writer(paid, *, inspect=None, send=None):
    """Trusted injections are Python-only, never public JSON authority flags."""
    inspect = inspect or (lambda: host_call('inspect'))
    send = send or (lambda request: host_call('complete', request))
    def write(stage, packet):
        metadata = inspect()
        if not metadata.get('ok'):
            raise WriterFailure(metadata.get('code', 'E_WRITER_CONFIG'))
        request = build_request(stage, packet, metadata.get('inputBudget'))
        def effect():
            try:
                response = send(request)
                if not response.get('ok'):
                    raise WriterFailure(response.get('code', 'E_WRITER_TRANSPORT'))
                cost = response.get('cost')
                if type(cost) not in (int, float) or not math.isfinite(cost) or cost < 0:
                    raise WriterFailure('E_WRITER_COST_UNKNOWN')
                if not isinstance(response.get('text'), str) or not response['text'].strip():
                    raise WriterFailure('E_WRITER_COMPLETION')
                return {'text': response['text'], 'cost': cost, 'inputBinding': request['inputBinding'], 'sourceBinding': request['sourceBinding']}
            except WriterFailure as exc:
                save(paid.ledger.path.parent/'errors'/('writer-'+request['inputBinding']+'.json'),
                     {**exc.diagnostic(), 'inputBinding':request['inputBinding'], 'sourceBinding':request['sourceBinding'], 'uncertain':True})
                raise
        result = paid.ledger.call('generation', [paid.scope, MODEL, BINDING, request], .10, effect, lambda r:r['cost'],
                                  account='subscription', model='Luna', request={'writerBinding': BINDING, 'inputBinding': request['inputBinding']})
        return parse_output(result['text'], stage, packet['unit']['source_ranges'])
    return write
