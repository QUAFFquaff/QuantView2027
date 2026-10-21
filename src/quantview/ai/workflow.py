from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from quantview.ai.provider import AIUnavailable, OpenAICompatibleProvider

ALLOWED_BIASES = {"bullish", "neutral", "bearish", "insufficient_data"}
OUTPUT_CONTRACT = {
    "bias": "bullish | neutral | bearish | insufficient_data",
    "confidence": "number from 0 to 1",
    "thesis": "concise Chinese research conclusion",
    "evidence_ids": ["IDs present in Evidence Pack only"],
    "counterarguments": ["strings"],
    "risk_flags": ["strings"],
    "invalidation_conditions": ["strings"],
}


def _validate_output(value: dict[str, Any], valid_evidence: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("AI output must be an object")
    bias = value.get("bias", "insufficient_data")
    if bias not in ALLOWED_BIASES:
        raise ValueError(f"Unsupported AI bias: {bias}")
    confidence = float(value.get("confidence", 0))
    confidence = min(1.0, max(0.0, confidence))
    evidence_ids = list(dict.fromkeys(value.get("evidence_ids") or []))
    unknown = set(evidence_ids) - valid_evidence
    if unknown:
        raise ValueError(f"AI cited unknown evidence IDs: {sorted(unknown)}")
    return {
        "bias": bias,
        "confidence": confidence,
        "thesis": str(value.get("thesis") or ""),
        "evidence_ids": evidence_ids,
        "counterarguments": [str(item) for item in value.get("counterarguments") or []],
        "risk_flags": [str(item) for item in value.get("risk_flags") or []],
        "invalidation_conditions": [
            str(item) for item in value.get("invalidation_conditions") or []
        ],
    }


class AIWorkflow:
    def __init__(self, root: Path, model: str, timeout: float = 90):
        self.root = root
        self.model = model
        self.provider = OpenAICompatibleProvider(model=model, timeout=timeout)

    def _prompt(self, role: str) -> str:
        return (self.root / "config" / "prompts" / f"{role}_v1.md").read_text(encoding="utf-8")

    def run(self, evidence_pack: dict[str, Any]) -> dict[str, Any]:
        valid_evidence = {item["evidence_id"] for item in evidence_pack["evidence"]}
        analyst_payload = {"evidence_pack": evidence_pack, "output_contract": OUTPUT_CONTRACT}
        bull = _validate_output(
            self.provider.complete_json(self._prompt("bull"), analyst_payload), valid_evidence
        )
        bear = _validate_output(
            self.provider.complete_json(self._prompt("bear"), analyst_payload), valid_evidence
        )
        judge_payload = {
            "evidence_pack": evidence_pack,
            "bull_analysis": bull,
            "bear_analysis": bear,
            "output_contract": OUTPUT_CONTRACT,
        }
        judge = _validate_output(
            self.provider.complete_json(self._prompt("judge"), judge_payload), valid_evidence
        )
        return {
            "analysis_id": (
                f"{evidence_pack['trade_date']}-{evidence_pack['product']}-"
                f"{evidence_pack['input_hash'][:16]}"
            ),
            "trade_date": evidence_pack["trade_date"],
            "product": evidence_pack["product"],
            "bull_case": json.dumps(bull, ensure_ascii=False),
            "bear_case": json.dumps(bear, ensure_ascii=False),
            "judge_view": judge["thesis"],
            "bias": judge["bias"],
            "confidence": judge["confidence"],
            "key_evidence": judge["evidence_ids"],
            "risk_flags": judge["risk_flags"],
            "invalidation_conditions": judge["invalidation_conditions"],
            "provider": self.provider.name,
            "model": self.model,
            "prompt_version": "v1",
            "input_hash": evidence_pack["input_hash"],
            "created_at": datetime.now().astimezone(),
        }


__all__ = ["AIUnavailable", "AIWorkflow"]
