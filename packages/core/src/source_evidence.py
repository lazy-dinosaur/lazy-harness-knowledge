"""Root-bound, fail-closed source evidence loading for the knowledge digestion review path.

This module reads only explicitly cited evidence inside a caller-supplied host root. It does
not judge meaning, follow URLs, search for a substitute, or turn unavailable evidence into a
negative result. Callers must persist the returned fingerprint with the exact fragment revisions
that were compared and revalidate both before applying a review result.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import urlparse

SUPPORTED = {"official_doc", "code_test", "user_utterance", "observed_output"}
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_BATCHES = 32
MAX_PACKET_BYTES = 8 * 1024 * 1024
MAX_BINDINGS = 512
MAX_SNAPSHOT_BYTES = 50 * 1024 * 1024
MAX_ORIGINAL_CONTEXT_CHARS = 180_000
_LINE_SUFFIX = re.compile(r"(?:#L|:L?)(\d+)(?:-L?(\d+))?$")


class SourceEvidenceError(ValueError):
    """Evidence failure with a stable, sanitized guard code for retry evidence."""
    def __init__(self, message, *, code="source_evidence_invalid"):
        super().__init__(message)
        self.code = code


def _inside(root: Path, path: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def load_import_bindings(host_root: str | Path, manifest_path: str | Path) -> dict:
    """Load explicit immutable snapshot bindings from a caller-selected import manifest.

    This never searches for snapshots or resolves missing original paths. The manifest and every
    packet/snapshot must be inside host_root, and each snapshot must match its recorded SHA-256.
    """
    try:
        root = Path(host_root).resolve(strict=True)
        manifest = Path(manifest_path)
        manifest = (root / manifest).resolve(strict=True) if not manifest.is_absolute() else manifest.resolve(strict=True)
    except OSError as exc:
        raise SourceEvidenceError("import manifest or host root is unavailable") from exc
    if not root.is_dir() or not _inside(root, manifest) or not manifest.is_file():
        raise SourceEvidenceError("import manifest is outside the explicit host root")
    try:
        if manifest.stat().st_size > MAX_MANIFEST_BYTES:
            raise SourceEvidenceError("import manifest exceeds the review bound")
        manifest_bytes = manifest.read_bytes()
        data = json.loads(manifest_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceEvidenceError("import manifest is invalid") from exc
    batches = data.get("batches")
    if not isinstance(batches, list) or len(batches) > MAX_BATCHES:
        raise SourceEvidenceError("import manifest batch count is invalid or exceeds the review bound")
    bindings = {}
    for batch in batches:
        if not isinstance(batch, dict):
            raise SourceEvidenceError("import manifest batch is malformed")
        packet_path = Path(batch.get("packet", ""))
        try:
            packet_path = (root / packet_path).resolve(strict=True) if not packet_path.is_absolute() else packet_path.resolve(strict=True)
        except OSError as exc:
            raise SourceEvidenceError("import packet is unavailable") from exc
        if not _inside(root, packet_path) or not packet_path.is_file():
            raise SourceEvidenceError("import packet escapes the explicit host root")
        try:
            if packet_path.stat().st_size > MAX_PACKET_BYTES:
                raise SourceEvidenceError("import packet exceeds the review bound")
            packet = json.loads(packet_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SourceEvidenceError("import packet is invalid") from exc
        source_map = packet.get("source_map")
        if not isinstance(source_map, list) or len(bindings) + len(source_map) > MAX_BINDINGS:
            raise SourceEvidenceError("import source-map count is invalid or exceeds the review bound")
        for entry in source_map:
            if not isinstance(entry, dict):
                raise SourceEvidenceError("import source map entry is malformed")
            original, snapshot, expected = entry.get("original"), entry.get("snapshot"), entry.get("sha256")
            if not isinstance(original, str) or not original or not isinstance(snapshot, str) or not isinstance(expected, str):
                raise SourceEvidenceError("import source map entry is incomplete")
            source_path = Path(snapshot)
            try:
                source_path = (root / source_path).resolve(strict=True) if not source_path.is_absolute() else source_path.resolve(strict=True)
            except OSError as exc:
                raise SourceEvidenceError("frozen import snapshot is unavailable") from exc
            if not _inside(root, source_path) or not source_path.is_file():
                raise SourceEvidenceError("frozen import snapshot escapes the explicit host root")
            if source_path.stat().st_size > MAX_SNAPSHOT_BYTES:
                raise SourceEvidenceError("frozen import snapshot exceeds the review bound")
            actual = hashlib.sha256(source_path.read_bytes()).hexdigest()
            if actual != expected:
                raise SourceEvidenceError("frozen import snapshot fingerprint changed")
            binding = {"path": source_path.relative_to(root).as_posix(), "expected_sha256": expected,
                       "origin": "frozen-import", "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest()}
            previous = bindings.get(original)
            if previous is not None and previous != binding:
                raise SourceEvidenceError("original locator has conflicting snapshot bindings")
            bindings[original] = binding
    return bindings


def read_reference(host_root: str | Path, reference: dict, *, context_lines: int = 8, bindings: dict | None = None) -> dict:
    """Read one exact evidence reference and return immutable comparison material.

    `user_utterance` is carried as its verbatim quote because it has no filesystem locator.
    File-backed evidence must resolve under `host_root`; URL fetching and parent/sibling lookup
    are intentionally unsupported. A declared quote must occur in the declared line range (or
    occur exactly once in the file when no line range is present).
    """
    if not isinstance(reference, dict):
        raise SourceEvidenceError("evidence reference must be an object")
    kind, locator, quote = reference.get("type"), reference.get("locator"), reference.get("quote")
    if kind not in SUPPORTED:
        raise SourceEvidenceError("unsupported source provenance")
    if not isinstance(locator, str) or not locator.strip() or not isinstance(quote, str) or not quote.strip():
        raise SourceEvidenceError("source locator and verbatim quote are required")
    if kind == "user_utterance":
        body = quote
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        return {"type": kind, "locator": locator, "quote": quote, "context": body,
                "fingerprint": digest, "origin": "verbatim-utterance", "line_start": None, "line_end": None}

    parsed = urlparse(locator)
    if parsed.scheme in ("http", "https") or "://" in locator:
        raise SourceEvidenceError("remote source is unavailable to root-bound review")
    try:
        root = Path(host_root).resolve(strict=True)
    except OSError as exc:
        raise SourceEvidenceError("host source root is unavailable") from exc
    if not root.is_dir():
        raise SourceEvidenceError("host source root is not a directory")
    section_locator = locator.endswith("#section")
    clean_locator = locator.split("#", 1)[0]
    match = _LINE_SUFFIX.search(clean_locator)
    if match:
        start = int(match.group(1))
        end = int(match.group(2) or start)
        clean_locator = clean_locator[:match.start()]
    else:
        start = end = None
    binding = (bindings or {}).get(clean_locator)
    origin = "live-root"
    if binding is not None:
        if not isinstance(binding, dict) or binding.get("origin") != "frozen-import":
            raise SourceEvidenceError("explicit source binding has an unsupported origin")
        candidate_locator = binding.get("path")
        expected_sha256 = binding.get("expected_sha256")
        if not isinstance(candidate_locator, str) or not isinstance(expected_sha256, str):
            raise SourceEvidenceError("explicit source binding is incomplete")
        origin = "frozen-import"
    else:
        candidate_locator, expected_sha256 = clean_locator, None
    try:
        candidate = (root / candidate_locator).resolve(strict=True)
    except OSError as exc:
        raise SourceEvidenceError("source file is unavailable", code="declared_source_file_unavailable") from exc
    if not _inside(root, candidate) or not candidate.is_file():
        raise SourceEvidenceError("source path is outside the host root or is not a file",
                                  code="declared_source_path_invalid")
    raw = candidate.read_bytes()
    fingerprint = hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None and fingerprint != expected_sha256:
        raise SourceEvidenceError("explicit source binding fingerprint changed", code="frozen_source_hash_mismatch")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SourceEvidenceError("source is not UTF-8 text") from exc
    lines = text.splitlines()
    reference_mode = "verbatim_quote"
    if section_locator:
        headings = [(i + 1, len(m.group(1)), m.group(2).strip())
                    for i, line in enumerate(lines)
                    if (m := re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line))]
        # A slash-bearing literal heading wins as a single title. Otherwise resolve the
        # breadcrumb against the actual Markdown parent chain (not merely ordered headings).
        literal = [heading for heading in headings if heading[2] == quote]
        matches = [[heading] for heading in literal]
        if not literal:
            components = quote.split(" / ")
            if len(components) > 8 or any(not part for part in components):
                raise SourceEvidenceError("section heading breadcrumb does not match source headings uniquely")
            stack = []
            ancestry = {}
            for heading in headings:
                line, level, _title = heading
                while stack and stack[-1][1] >= level:
                    stack.pop()
                ancestry[line] = [*stack, heading]
                stack.append(heading)
            matches = [chain for chain in ancestry.values()
                       if [item[2] for item in chain[-len(components):]] == components
                       and len(chain) >= len(components)]
        if len(matches) != 1:
            raise SourceEvidenceError("section heading breadcrumb does not match source headings uniquely",
                                      code="section_breadcrumb_unmatched_or_ambiguous")
        selected = matches[0]
        start = selected[-1][0]
        section_level = selected[-1][1]
        end = next((line - 1 for line, level, _ in headings if line > start and level <= section_level), len(lines))
        if end - start + 1 > 120:
            raise SourceEvidenceError("section context exceeds the source-review bound")
        reference_mode = "section_heading"
    elif start is None:
        hits = [i for i, line in enumerate(lines) if quote in line]
        if len(hits) != 1:
            raise SourceEvidenceError("citation is absent or ambiguous without an exact line range",
                                      code="quote_absent_or_ambiguous")
        start = end = hits[0] + 1
    if start < 1 or end < start or end > len(lines):
        raise SourceEvidenceError("source line range is invalid", code="source_line_range_invalid")
    cited = "\n".join(lines[start - 1:end])
    if reference_mode == "verbatim_quote" and quote not in cited:
        raise SourceEvidenceError("verbatim quote is not present in the declared source range",
                                  code="quote_not_in_declared_range")
    if reference_mode == "section_heading":
        resolved_titles = [h[2] for h in selected] if literal else [h[2] for h in selected[-len(components):]]
        if " / ".join(resolved_titles) != quote:
            raise SourceEvidenceError("section heading breadcrumb is not verbatim", code="section_breadcrumb_not_verbatim")
    lo, hi = (start, end) if section_locator else (max(1, start - context_lines), min(len(lines), end + context_lines))
    context = "\n".join(f"{n}: {lines[n - 1]}" for n in range(lo, hi + 1))
    return {"type": kind, "locator": locator, "quote": quote, "context": context,
            "fingerprint": fingerprint, "origin": origin, "reference_mode": reference_mode,
            "line_start": start, "line_end": end,
            "resolved_path": candidate.relative_to(root).as_posix(),
            **({"manifest_sha256": binding["manifest_sha256"]} if binding is not None else {})}


def read_declared_original_contexts(host_root: str | Path, source: dict, *, bindings: dict | None = None,
                                   max_chars: int = MAX_ORIGINAL_CONTEXT_CHARS) -> dict:
    """Read bounded original documents named by existing refs, even when their quote/anchor is stale.

    This is context recovery, not citation verification: callers must retain the status as
    unverified and may not use this result as an apply-ready proof. Only exact declared locators
    or exact manifest source-map identities are accepted; there is no filename/sibling search.
    """
    if not isinstance(source, dict) or not isinstance(source.get("evidence_refs"), list):
        raise SourceEvidenceError("declared original source references are unavailable")
    root = Path(host_root).resolve(strict=True)
    contexts, total, seen_documents = [], 0, set()
    for ref in source["evidence_refs"]:
        if not isinstance(ref, dict) or ref.get("type") not in ("official_doc", "code_test"):
            continue
        locator = ref.get("locator")
        if not isinstance(locator, str) or not locator.strip() or "://" in locator:
            continue
        clean = locator.split("#", 1)[0]
        match = _LINE_SUFFIX.search(clean)
        if match:
            clean = clean[:match.start()]
        binding = (bindings or {}).get(clean)
        path = binding.get("path") if isinstance(binding, dict) else clean
        if not isinstance(path, str) or not path:
            continue
        try:
            candidate = (root / path).resolve(strict=True)
            if not _inside(root, candidate) or not candidate.is_file():
                continue
            raw = candidate.read_bytes()
            if len(raw) > MAX_SNAPSHOT_BYTES:
                continue
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        fingerprint = hashlib.sha256(raw).hexdigest()
        if binding is not None and (binding.get("origin") != "frozen-import"
                or fingerprint != binding.get("expected_sha256")):
            raise SourceEvidenceError("declared original source binding changed")
        document_key = (candidate.relative_to(root).as_posix(),
                        binding.get("origin", "live-root") if isinstance(binding, dict) else "live-root", fingerprint)
        if document_key in seen_documents:
            continue
        seen_documents.add(document_key)
        if total + len(text) > max_chars:
            raise SourceEvidenceError("declared original context exceeds the recovery bound",
                                      code="declared_original_context_limit")
        total += len(text)
        contexts.append({"type": ref["type"], "declared_locator": clean, "resolved_path": candidate.relative_to(root).as_posix(),
            "origin": binding.get("origin", "live-root") if isinstance(binding, dict) else "live-root",
            "fingerprint": fingerprint, "context": text,
            **({"manifest_sha256": binding.get("manifest_sha256")} if isinstance(binding, dict) else {})})
    if not contexts:
        raise SourceEvidenceError("no declared original document is available",
                                  code="declared_original_document_unavailable")
    canonical = "\\n".join(f"{c['declared_locator']}\\0{c['origin']}\\0{c['fingerprint']}" for c in contexts)
    return {"evidence": contexts, "fingerprint": hashlib.sha256(canonical.encode()).hexdigest(),
            "verification": "unverified-original-context"}


def literal_quote_references(contexts: list[dict], quotes: list[str], *, max_candidates: int = 12) -> list[dict]:
    """Enumerate exact literal quote coordinates in already-authorized documents; never rank candidates."""
    if not isinstance(contexts, list) or not isinstance(quotes, list) or not quotes:
        raise SourceEvidenceError("exact quote coordinates require source contexts and quotes")
    refs = []
    for quote in quotes:
        if not isinstance(quote, str) or not quote.strip():
            raise SourceEvidenceError("source quote must be nonempty text")
        matches = []
        for item in contexts:
            text = item["context"]
            offset = 0
            while True:
                found = text.find(quote, offset)
                if found < 0:
                    break
                line_start = text.count("\n", 0, found) + 1
                line_end = line_start + quote.count("\n")
                matches.append((item, line_start, line_end))
                offset = found + 1
                if len(matches) > max_candidates:
                    raise SourceEvidenceError("exact quote candidate count exceeds the recovery bound")
        if not matches:
            raise SourceEvidenceError("generated quote is absent from declared original documents")
        for item, line_start, line_end in matches:
            span = f"L{line_start}" if line_start == line_end else f"L{line_start}-L{line_end}"
            refs.append({"type": item["type"], "locator": f"{item['declared_locator']}:{span}", "quote": quote,
                         "_origin": item["origin"], "_fingerprint": item["fingerprint"]})
    return refs


def read_fragment_sources(host_root: str | Path, source: dict, *, limit: int = 12, bindings: dict | None = None) -> dict:
    """Load all cited, supported references for one fragment or fail the entire bundle closed."""
    if not isinstance(source, dict):
        raise SourceEvidenceError("fragment source is missing")
    refs = source.get("evidence_refs")
    if not isinstance(refs, list) or not refs:
        raise SourceEvidenceError("fragment has no evidence references")
    if any(not isinstance(ref, dict) or ref.get("type") not in SUPPORTED for ref in refs):
        raise SourceEvidenceError("fragment includes unsupported source provenance")
    selected = list(refs)
    if not selected or len(selected) > limit:
        raise SourceEvidenceError("no supported citations or citation count exceeds the review bound")
    evidence = [read_reference(host_root, ref, bindings=bindings) for ref in selected]
    canonical = "\n".join(f"{e['type']}\0{e['locator']}\0{e['origin']}\0{e['fingerprint']}\0{e['quote']}" for e in evidence)
    return {"evidence": evidence, "fingerprint": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}
