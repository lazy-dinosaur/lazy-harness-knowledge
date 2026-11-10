"""Loopback-only E5 HTTP service; run with the pinned model venv Python."""
import argparse
import json
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import embed

MAX_TEXTS = 64
MAX_CHARS = 4096
MAX_BODY = MAX_TEXTS * MAX_CHARS * 4 + 4096
DEFAULT_PORT = 8765


class Encoder:
    def __init__(self):
        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        embed._verify()
        self.np = np
        self.tokenizer = Tokenizer.from_file(str(embed._MODEL_DIR / "tokenizer.json"))
        self.tokenizer.enable_padding(pad_id=1, pad_token="<pad>")
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self.session = ort.InferenceSession(str(embed._MODEL_DIR / "model.onnx"), sess_options=options,
                                            providers=["CPUExecutionProvider"])
        self.loaded_at = datetime.now(timezone.utc).isoformat()

    def encode(self, texts, role):
        if role not in ("passage", "query") or not isinstance(texts, list) or not 1 <= len(texts) <= MAX_TEXTS or any(
            not isinstance(text, str) or len(text) > MAX_CHARS for text in texts
        ):
            raise ValueError("invalid role or texts (1..64 strings, max 4096 characters each)")
        np = self.np
        encodings = self.tokenizer.encode_batch([role + ": " + text for text in texts])
        if any(len(item.ids) > 512 for item in encodings):
            raise ValueError("E5 input exceeds 512 tokens; truncation forbidden")
        arrays = {"input_ids": np.asarray([e.ids for e in encodings], dtype=np.int64),
                  "attention_mask": np.asarray([e.attention_mask for e in encodings], dtype=np.int64),
                  "token_type_ids": np.asarray([e.type_ids for e in encodings], dtype=np.int64)}
        hidden = self.session.run(None, {item.name: arrays[item.name] for item in self.session.get_inputs()})[0]
        if hidden.ndim != 3 or hidden.shape[2] != 384:
            raise RuntimeError("unexpected E5 output dimensions")
        mask = arrays["attention_mask"][..., None].astype(np.float32)
        pooled = (hidden * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1)
        vectors = pooled / np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12)
        if not np.isfinite(vectors).all() or not np.allclose(np.linalg.norm(vectors, axis=1), 1, atol=1e-5):
            raise RuntimeError("invalid E5 vector")
        return vectors.tolist()


def serve(port=DEFAULT_PORT, host="127.0.0.1"):
    if host != "127.0.0.1":
        raise ValueError("embedding server must bind to 127.0.0.1")
    encoder = Encoder()

    class Handler(BaseHTTPRequestHandler):
        def respond(self, code, value):
            body = json.dumps(value).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path != "/health":
                return self.respond(404, {"error": "not found"})
            self.respond(200, {"model_id": embed.MODEL_ID, "dim": 384, "loaded_at": encoder.loaded_at})

        def do_POST(self):
            if self.path != "/embed":
                return self.respond(404, {"error": "not found"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= MAX_BODY:
                    raise ValueError("invalid body size")
                payload = json.loads(self.rfile.read(size))
                if not isinstance(payload, dict):
                    raise ValueError("body must be an object")
                vectors = encoder.encode(payload.get("texts"), payload.get("role"))
            except (ValueError, TypeError, UnicodeDecodeError) as error:
                return self.respond(400, {"error": str(error)})
            self.respond(200, {"model_id": embed.MODEL_ID, "vectors": vectors})

    with ThreadingHTTPServer((host, port), Handler) as server:
        server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=int(os.environ.get("LH_EMBED_PORT", DEFAULT_PORT)))
    args = parser.parse_args()
    serve(args.port, args.host)
