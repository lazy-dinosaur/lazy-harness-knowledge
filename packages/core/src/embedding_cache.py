"""Exact model/role/text cache. Atomic private files, no DB or provider dependency."""
import hashlib
import json
import math
import os
from pathlib import Path
import threading


def key(model, role, text):
    if role not in ('query', 'passage') or not isinstance(text, str):
        raise ValueError('invalid embedding input')
    sha = hashlib.sha256(text.encode('utf-8')).hexdigest()
    return hashlib.sha256(json.dumps([model, role, sha], ensure_ascii=False).encode()).hexdigest()


class VectorCache:
    def __init__(self, directory=None):
        self.directory = Path(directory) if directory else None
        self.memory = {}
        self.lock = threading.RLock()
        self.hits = self.misses = 0

    def get(self, model, role, text):
        ident = key(model, role, text)
        with self.lock:
            if ident not in self.memory and self.directory:
                path = self.directory / (ident + '.json')
                if path.exists():
                    row = json.loads(path.read_text())
                    vector = row['vector']
                    if row['key'] != ident or row['sha256'] != self._sha(vector):
                        raise ValueError('embedding cache integrity mismatch')
                    self._validate(vector)
                    self.memory[ident] = vector
            return self.memory.get(ident)

    @staticmethod
    def _sha(vector):
        return hashlib.sha256(json.dumps(vector).encode()).hexdigest()

    @staticmethod
    def _validate(vector):
        if not isinstance(vector, list) or not vector or any(type(v) not in (int, float) or not math.isfinite(v) for v in vector):
            raise ValueError('invalid cached embedding vector')

    def put_many(self, model, role, texts, vectors):
        if len(texts) != len(vectors):
            raise ValueError('embedding result count mismatch')
        with self.lock:
            for text, vector in zip(texts, vectors):
                self._validate(vector)
                ident = key(model, role, text)
                if self.directory:
                    self.directory.mkdir(parents=True, exist_ok=True)
                    path = self.directory / (ident + '.json')
                    tmp = path.with_suffix('.tmp')
                    with tmp.open('w') as stream:
                        os.chmod(tmp, 0o600)
                        json.dump({'key':ident, 'vector':vector, 'sha256':self._sha(vector)}, stream)
                        stream.flush(); os.fsync(stream.fileno())
                    os.replace(tmp, path)
                self.memory[ident] = list(vector)

    def stats(self):
        return {'hits':self.hits, 'misses':self.misses}
