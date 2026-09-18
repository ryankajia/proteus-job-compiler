"""Optional Claude API layer (L3/L4 live paths).

Kept optional so the MVP runs on the standard library alone. When the
`anthropic` SDK and a key are present, this drives calibration re-assessment and
richer JD parsing.

Model choice: claude-fable-5 — Anthropic's most capable model, suited to the
evidence-weighing judgement Calibration needs. Per the Fable 5 API surface:
  - thinking is always on; do NOT pass a `thinking` param.
  - control depth with output_config.effort.
  - opt into server-side refusal fallbacks by default so a false-positive
    classifier decline (benign career/CV content can trip these) is transparently
    re-served by claude-opus-4-8 in the same call instead of failing.
"""
from __future__ import annotations

import json
import os

MODEL = "claude-fable-5"
FALLBACK = "claude-opus-4-8"


def available() -> bool:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


def _client():
    import anthropic
    return anthropic.Anthropic()


def _complete(system: str, user: str, effort: str = "high", max_tokens: int = 2000) -> str:
    client = _client()
    resp = client.beta.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        betas=["server-side-fallback-2026-06-01"],
        fallbacks=[{"model": FALLBACK}],
        output_config={"effort": effort},
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("Claude declined the request (both primary and fallback).")
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")


def _extract_json(text: str):
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        return json.loads(text[start:end + 1])
    raise ValueError("No JSON object found in model output.")


def assess_level(cap: dict, evidence: list[dict]) -> tuple[float, str]:
    """Re-derive an evidence-driven level (1-5) + rationale for one capability."""
    from .calibration import RUBRIC
    ev_txt = "\n".join(f"- ({e['type']}) {e['ref']}" for e in evidence) or "(no evidence on file)"
    system = (
        "You are a calibration assessor for a job-application system. You rate a "
        "person's true skill level from evidence only — never from self-report. "
        "Guard equally against over-reach and systematic self-underestimation. "
        f"Scale: {RUBRIC} Reply with JSON only: "
        '{"level": <number 1-5>, "rationale": "<one sentence>"}.'
    )
    user = f"Capability: {cap['statement']}\nKeywords: {', '.join(cap['keywords'])}\nEvidence:\n{ev_txt}"
    data = _extract_json(_complete(system, user, effort="high", max_tokens=600))
    return float(data["level"]), str(data["rationale"])


def parse_jd(raw: str, source: str = "manual") -> dict:
    """Parse a pasted JD into the JobSpec shape via Claude."""
    system = (
        "Extract a structured JobSpec from the job description. Reply with JSON "
        "only, keys: title, org, seniority, required_skills (array), "
        "nice_to_have (array), visa_requirement (string), security_clearance "
        "(0 or 1), salary, deadline, apply_channel. Do not invent facts."
    )
    data = _extract_json(_complete(system, raw, effort="medium", max_tokens=1500))
    data.setdefault("required_skills", [])
    data.setdefault("nice_to_have", [])
    data["security_clearance"] = int(data.get("security_clearance", 0) or 0)
    data["source"] = source
    data["raw_text"] = raw.strip()
    return data
