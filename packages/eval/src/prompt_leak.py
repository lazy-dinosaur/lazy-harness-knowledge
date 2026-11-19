"""Host-neutrality check for the module's model prompts (user 2026-11-14: the module is universal, prompts carry no host data).

Scans every module-level prompt-like string (>= 60 chars) in packages/core/src against a HOST corpus given at run time
(never stored here) and reports: code-shaped backtick values of the corpus (digits, '/', '.', '_' or camelCase, >= 4 chars)
that appear in a prompt, English 5-word phrases of the corpus, and quoted examples (>= 10 chars) found verbatim in the corpus.

Usage: python3 packages/eval/src/prompt_leak.py --corpus <dir of host .md files>   (exit 2 when a leak is found)
"""
import argparse, importlib, pathlib, re, sys

CODE_SHAPE = re.compile(r'[0-9/._]|[a-z][A-Z]')


def prompts(src_dir):
    sys.path.insert(0, str(src_dir))
    out = {}
    for p in sorted(pathlib.Path(src_dir).glob('*.py')):
        if p.stem.startswith('_'):
            continue
        try:
            mod = importlib.import_module(p.stem)
        except Exception:
            continue
        for name, val in vars(mod).items():
            if name.startswith('__'):
                continue
            vals = [val] if isinstance(val, str) else [v for v in val.values() if isinstance(v, str)] if isinstance(val, dict) else []
            for i, v in enumerate(vals):
                if len(v) >= 60:
                    out['%s.%s%s' % (p.stem, name, '' if isinstance(val, str) else '[%d]' % i)] = v
    return out


def scan(corpus, texts):
    codes = {c for c in re.findall(r'`([^`\n]{4,80})`', corpus) if CODE_SHAPE.search(c)}
    hits = {}
    for key, text in texts.items():
        found = {'`%s`' % c for c in codes if c in text}
        toks = re.findall(r'[A-Za-z][A-Za-z\'-]+', text)
        for i in range(len(toks) - 4):
            phrase = ' '.join(toks[i:i + 5])
            if phrase in corpus:
                found.add(phrase)
        for q in re.findall(r'["“‘\'](([^"”’\'\n]{10,80}))["”’\']', text):
            if re.search(r'[A-Za-z가-힣]', q[0]) and q[0] in corpus:
                found.add('"%s"' % q[0])
        if found:
            hits[key] = sorted(found)
    return hits


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--corpus', required=True)
    ap.add_argument('--src', default=str(pathlib.Path(__file__).resolve().parents[2] / 'core' / 'src'))
    a = ap.parse_args(argv)
    corpus = '\n'.join(p.read_text(errors='ignore') for p in pathlib.Path(a.corpus).rglob('*.md'))
    hits = scan(corpus, prompts(a.src))
    for k, v in sorted(hits.items()):
        print('LEAK %s: %s' % (k, v[:20]))
    print('prompt_leak: %d prompt(s) with host terms' % len(hits))
    raise SystemExit(2 if hits else 0)


if __name__ == '__main__':
    main()
