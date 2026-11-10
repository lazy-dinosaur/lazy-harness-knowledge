"""Received-error evidence only. Status alone NEVER proves a free Jev request.
No tariff assertion: Main runtime may perform one read-only generation lookup,
or use a saved provider receipt under jev-generation-evidence/<id>.json.
"""
import json
import re
from urllib.error import HTTPError
from safety import Stop, load, save


def capture(error):
    if hasattr(error, '_jev_evidence'):
        return error._jev_evidence
    retry_after = None
    if isinstance(error, HTTPError):
        status, url = error.code, error.url
        try:
            raw = error.read().decode('utf-8', errors='replace')
        except Exception:
            raw = '[response body unavailable]'
    else:
        import httpx
        if not isinstance(error, httpx.HTTPStatusError):
            return None
        response = error.response
        status, url, raw = response.status_code, str(response.url), response.text
        retry_after = response.headers.get('Retry-After')
    try:
        body = json.loads(raw)
    except ValueError:
        body = None
    generation = None
    if isinstance(body, dict):
        error_body = body.get('error')
        metadata = error_body.get('metadata') if isinstance(error_body, dict) else None
        generation = body.get('id') or (metadata.get('generation_id') if isinstance(metadata, dict) else None)
    if not isinstance(generation, str) or not re.fullmatch(r'gen-[A-Za-z0-9_-]+', generation):
        generation = None
    error._jev_evidence = {'received_response':True, 'status':status, 'url':url,
                           'body':raw, 'generation_id':generation, 'retry_after':retry_after}
    return error._jev_evidence


def uncharged(evidence, state, base='https://openrouter.ai/api', *, endpoint='/v1/systemone'):
    if not evidence or evidence.get('received_response') is not True or type(evidence.get('status')) is not int:
        return False
    if not (400 <= evidence['status'] <= 599 or (endpoint == '/v1/embeddings' and evidence.get('provider_rate_limit') is True)):
        return False
    if evidence.get('url') != base.rstrip('/') + endpoint:
        return False
    generation = evidence.get('generation_id')
    if not isinstance(generation, str) or not re.fullmatch(r'gen-[A-Za-z0-9_-]+', generation):
        return False
    path = state / 'jev-generation-evidence' / (generation + '.json')
    if not path.exists():
        return False
    try:
        receipt = load(path)
    except (OSError, ValueError):
        return False
    if not isinstance(receipt, dict) or not isinstance(receipt.get('body'), dict):
        return False
    data = receipt['body'].get('data')
    if not isinstance(data, dict):
        return False
    # Main-saved provider receipt, not the error body's self-reported usage/HTTP status.
    return (receipt.get('status') == 200 and receipt.get('url') == base.rstrip('/') + '/v1/generation?id=' + generation
            and data.get('id') == generation and type(data.get('total_cost')) in (int, float)
            and data['total_cost'] == 0
            and data.get('finish_reason') in ('error','stop','length','tool_calls','content_filter'))


def resolve(ledger, ident, reason, *, base='https://openrouter.ai/api', require_uncharged=False, count_as_spent=False):
    if not reason or not reason.strip():
        raise Stop('explicit resolution reason required')
    with ledger.lock:
        rows = ledger._effect_rows(ident)
        if not rows or rows[-1]['state'] != 'reserved':
            raise Stop('only a retained reservation can be resolved')
        row = rows[-1]
        if row.get('account', 'cash') != 'cash' or row['step'] in ('embedding', 'generation'):
            raise Stop('Jev resolution only; never replay subscription models')
        path = ledger.path.parent / 'errors' / (ident + '.json')
        evidence = load(path) if path.exists() else None
        free = not count_as_spent and uncharged(evidence, ledger.path.parent, base)
        if require_uncharged and not free:
            raise Stop('no definite uncharged evidence')
        resolution = {'id':ident, 'step':row['step'], 'state':'released',
                      'account':'cash', 'charge':0 if free else row['charge'],
                      'resolution':'jev-uncharged' if free else 'jev-conservative-spent',
                      'reason':reason, 'error_evidence':evidence,
                      'billing_basis':'matched generation total_cost=0' if free else 'no verified zero-charge evidence; full reservation counted as spent'}
        ledger.append(resolution)
        save(ledger.path.parent / 'resolutions' / (ident + '.json'), resolution)
        return resolution
