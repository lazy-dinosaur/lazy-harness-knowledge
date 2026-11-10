"""Run scopes and their homes (core configuration). Experiment preparation lives in prepare.py."""
from safety import HERE

SCOPES = ('full', 'pilot', 'pilot-fresh', 'pilot-feature', 'pilot-area-10', 'pilot-area',
          'pilot-area-10-isolated', 'pilot-area-10-unit1-current')
AREA_SCOPES = ('pilot-area-10', 'pilot-area', 'pilot-area-10-isolated', 'pilot-area-10-unit1-current')
TEN_SCOPES = ('pilot-area-10', 'pilot-area-10-isolated', 'pilot-area-10-unit1-current')
PRIOR_REQUIRED_SCOPES = ('pilot-area-10-isolated', 'pilot-area-10-unit1-current')
FRESH_SCOPES = tuple(s for s in SCOPES if s not in ('full', 'pilot'))

def scope_home(scope):
    if scope not in SCOPES: raise ValueError('invalid scope')
    home = HERE/scope if scope != 'full' else HERE
    if scope != 'full' and home.resolve() != HERE.resolve()/scope:
        raise ValueError('scope must remain inside the source root')
    return home

def source_home(home):
    return home.parent if home.name in SCOPES[1:] else home

def default_runs(scope):
    if scope not in SCOPES: raise ValueError('invalid scope')
    return 1 if scope != 'full' else 3
