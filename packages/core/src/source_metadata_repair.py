"""Source-proven metadata-only citation repair. This never judges claim semantics or edits claim text."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import source_evidence

ACTOR = "source-metadata-repair"


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode("utf-8")).hexdigest()


def _claim_identity(fragment: dict) -> dict:
    # Fields which make the existing canonical assertion the same assertion. Citation repair
    # may not revise any of them; the DB writer CASes this complete identity.
    names = ("text", "subject", "subject_id", "alias", "domain", "kind", "basis", "evidence_source",
             "human_status", "confidence", "active")
    return {key: fragment.get(key) for key in names if key in fragment}


def prepare(fragment: dict, new_refs: list, host_root: str | Path, *, bindings: dict | None = None,
            expected_host: str, source_verification: dict | None = None) -> dict:
    """Verify replacement citations and preserve the complete old source metadata for audit.

    Invalid legacy references are retained verbatim in the proposal's old snapshot. Every new
    reference must independently resolve and the replacement bundle must pass as a whole.
    Quote validity is evidence integrity only, never semantic approval.
    """
    if (not isinstance(fragment, dict) or not isinstance(fragment.get("id"), str)
            or not isinstance(fragment.get("revision"), int) or isinstance(fragment.get("revision"), bool)
            or fragment["revision"] < 1 or not isinstance(fragment.get("text"), str)
            or not isinstance(expected_host, str) or not expected_host
            or not isinstance(new_refs, list) or not new_refs):
        raise source_evidence.SourceEvidenceError("invalid metadata-repair identity or replacement references")
    root = Path(host_root).resolve(strict=True)
    old_source = fragment.get("source")
    if not isinstance(old_source, dict):
        raise source_evidence.SourceEvidenceError("old source metadata is unavailable")
    old_refs = old_source.get("evidence_refs")
    if not isinstance(old_refs, list) or not old_refs:
        raise source_evidence.SourceEvidenceError("old citation bundle is unavailable")
    # Read each previous locator separately. A broken old citation is evidence to preserve, not
    # a reason to waive validation of any replacement reference.
    old_checks = []
    for ref in old_refs:
        try:
            old_checks.append({"status": "verified", "proof": source_evidence.read_reference(root, ref, bindings=bindings)})
        except (source_evidence.SourceEvidenceError, TypeError) as exc:
            old_checks.append({"status": "unavailable", "error": type(exc).__name__, "reference": ref})
    if len(new_refs) != len(old_refs):
        raise source_evidence.SourceEvidenceError("replacement citation bundle must account for every existing reference")
    if any(not isinstance(ref, dict) or ref.get("type") == "user_utterance" for ref in new_refs):
        raise source_evidence.SourceEvidenceError("citation repair requires source-origin evidence, not human-origin evidence")
    replacement = {"evidence_refs": new_refs}
    verified = source_evidence.read_fragment_sources(root, replacement, bindings=bindings)
    if not verified["evidence"]:
        raise source_evidence.SourceEvidenceError("replacement bundle has no verified source context")
    origins = {entry["origin"] for entry in verified["evidence"]}
    if len(origins) != 1:
        raise source_evidence.SourceEvidenceError("metadata repair requires one explicit source origin")
    if source_verification is not None:
        if (not isinstance(source_verification, dict) or source_verification.get("status") != "supported"
                or source_verification.get("fragment_id") != fragment["id"]
                or source_verification.get("revision") != fragment["revision"]
                or source_verification.get("source_fingerprint") != verified["fingerprint"]):
            raise source_evidence.SourceEvidenceError("current claim source verification does not match repaired proof",
                                                      code="metadata_semantic_proof_mismatch")
    proposal = {
        "operation": "metadata_repair", "actor": ACTOR, "host_id": expected_host,
        "fragment_id": fragment["id"], "expected_revision": fragment["revision"],
        "claim_identity": _claim_identity(fragment), "claim_identity_hash": _digest(_claim_identity(fragment)),
        "old_source": old_source, "old_source_hash": _digest(old_source), "old_refs": old_refs,
        "old_reference_checks": old_checks, "new_refs": new_refs,
        "new_bundle_fingerprint": verified["fingerprint"], "new_source_contexts": verified["evidence"],
        "source_root": str(root), "source_root_hash": hashlib.sha256(str(root).encode()).hexdigest(),
        "source_origin": next(iter(origins)), "human_status_unchanged": True,
        **({"source_verification": source_verification} if source_verification is not None else {}),
    }
    proposal["repair_key"] = _digest(proposal)
    return proposal


def validate_request(proposal: dict, *, host: str, host_root: str | Path) -> Path:
    """Check immutable operation identity before either apply or idempotent replay."""
    if not isinstance(proposal, dict) or proposal.get("operation") != "metadata_repair" or proposal.get("actor") != ACTOR:
        raise ValueError("trusted source metadata-repair proposal required")
    unsigned = {k: v for k, v in proposal.items() if k != "repair_key"}
    if _digest(unsigned) != proposal.get("repair_key"):
        raise ValueError("metadata-repair identity key mismatch")
    root = Path(host_root).resolve(strict=True)
    if (proposal.get("host_id") != host or proposal.get("source_root") != str(root)
            or proposal.get("source_root_hash") != hashlib.sha256(str(root).encode()).hexdigest()):
        raise ValueError("metadata-repair host or source root mismatch")
    return root


def validate_proposal(proposal: dict, *, host: str, fragment: dict, host_root: str | Path,
                      bindings: dict | None = None) -> dict:
    """Revalidate all root/source proof and exact CAS values immediately before the DB write."""
    root = validate_request(proposal, host=host, host_root=host_root)
    if (fragment.get("id") != proposal.get("fragment_id")
            or fragment.get("revision") != proposal.get("expected_revision")
            or fragment.get("text") != proposal.get("claim_identity", {}).get("text")
            or _claim_identity(fragment) != proposal.get("claim_identity")
            or _digest(_claim_identity(fragment)) != proposal.get("claim_identity_hash")
            or fragment.get("source") != proposal.get("old_source")
            or _digest(fragment.get("source")) != proposal.get("old_source_hash")):
        raise ValueError("metadata-repair old claim/source metadata changed")
    checked = source_evidence.read_fragment_sources(root, {"evidence_refs": proposal.get("new_refs")}, bindings=bindings)
    if checked["fingerprint"] != proposal.get("new_bundle_fingerprint") or checked["evidence"] != proposal.get("new_source_contexts"):
        raise ValueError("metadata-repair replacement source proof changed")
    return checked
