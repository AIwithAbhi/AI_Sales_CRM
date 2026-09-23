"""Configurable scoring profiles — load, prompt template, parse, Airtable columns."""

from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_PATH = os.path.join(_ROOT, "config", "scoring_profiles.json")


def _clamp_score_1_10(value: Any, default: int = 1) -> int:
    try:
        n = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(1, min(10, n))


def _normalize_confidence(value: Any, default: str = "low") -> str:
    conf = str(value or "").strip().lower()
    if conf in ("high", "medium", "low"):
        return conf
    return default


@lru_cache(maxsize=1)
def load_scoring_config() -> Dict[str, Any]:
    """Load scoring profile config from JSON (cached)."""
    with open(_CONFIG_PATH, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data.get("profiles"), list) or not data["profiles"]:
        raise ValueError("scoring_profiles.json must contain a non-empty profiles list")
    return data


def list_profiles() -> List[Dict[str, Any]]:
    """Public summary of profiles for the frontend selector."""
    cfg = load_scoring_config()
    out: List[Dict[str, Any]] = []
    for p in cfg["profiles"]:
        out.append(
            {
                "id": p["id"],
                "name": p["name"],
                "short_name": p.get("short_name") or p["name"],
                "description": p.get("description") or "",
                "dimension_ids": [d["id"] for d in p.get("dimensions") or []],
                "dimension_names": [d["name"] for d in p.get("dimensions") or []],
            }
        )
    return out


def default_profile_ids() -> List[str]:
    cfg = load_scoring_config()
    ids = cfg.get("default_profile_ids") or []
    if ids:
        return list(ids)
    return [cfg["profiles"][0]["id"]]


def get_profile(profile_id: str) -> Optional[Dict[str, Any]]:
    for p in load_scoring_config()["profiles"]:
        if p["id"] == profile_id:
            return p
    return None


def resolve_profile_ids(raw: Optional[Sequence[str]]) -> List[str]:
    """
    Normalize requested profile IDs.
    Unknown IDs are dropped; empty → defaults.
    Preserves order, dedupes.
    """
    known = {p["id"] for p in load_scoring_config()["profiles"]}
    if not raw:
        return default_profile_ids()
    seen = set()
    out: List[str] = []
    for item in raw:
        pid = str(item or "").strip()
        if not pid or pid in seen or pid not in known:
            continue
        seen.add(pid)
        out.append(pid)
    return out or default_profile_ids()


def _format_few_shot(profile: Dict[str, Any], shot: Dict[str, Any], index: int) -> str:
    labels = {0: "strong", 1: "poor", 2: "mixed"}
    label = shot.get("label") or labels.get(index, f"example_{index + 1}")
    lines = [
        f"Example ({label}) for profile '{profile['id']}':",
        f"Homepage text: {shot.get('homepage_text', '').strip()}",
        "Expected dimensions:",
    ]
    expected = shot.get("expected") or {}
    for dim in profile.get("dimensions") or []:
        dim_id = dim["id"]
        exp = expected.get(dim_id) or {}
        lines.append(
            f'  {dim_id}: score={exp.get("score")}, confidence="{exp.get("confidence")}", '
            f'reason="{exp.get("reason", "")}"'
        )
    return "\n".join(lines)


def build_profile_prompt_section(profile: Dict[str, Any]) -> str:
    """Build the scorecard prompt block for one profile."""
    pid = profile["id"]
    lines: List[str] = [
        f"SCORING PROFILE: {profile['name']} (id: {pid})",
        str(profile.get("description") or "").strip(),
        "",
        "Score EACH dimension 1-10 using ONLY homepage text. Prefer lower scores when evidence is thin.",
        "Missing evidence is NOT proof of absence — say so explicitly in the reason when thin.",
        "",
        f'Return results under JSON path: profile_scores["{pid}"]["dimensions"]["<dimension_id>"]',
        "Each dimension object MUST have:",
        '  - score: integer 1-10',
        '  - confidence: "high" | "medium" | "low" (EVIDENCE QUALITY, independent of how high/low the score is)',
        "  - reason: 1-2 sentences that MUST quote or closely paraphrase the SPECIFIC scraped phrase.",
        '    If none, say "no evidence found in available content" or '
        '"limited evidence available from homepage content".',
        "",
        "Dimensions:",
    ]
    for dim in profile.get("dimensions") or []:
        lines.append(f"- {dim['name']} (id: {dim['id']}):")
        lines.append(f"  Evaluate: {dim.get('description', '')}")
        hints = dim.get("evidence_hints") or []
        if hints:
            lines.append("  Look for:")
            for h in hints:
                lines.append(f"    * {h}")
        if dim.get("rubric"):
            lines.append(f"  Rubric: {dim['rubric']}")
        lines.append("")

    shots = profile.get("few_shots") or []
    if shots:
        lines.append(
            f"FEW-SHOT CALIBRATION for {profile['name']} "
            "(study before scoring; do NOT copy these scores):"
        )
        lines.append("")
        for i, shot in enumerate(shots):
            lines.append(_format_few_shot(profile, shot, i))
            lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def build_system_prompt(base_prompt: str, profile_ids: Optional[Sequence[str]] = None) -> str:
    """
    Append selected scoring-profile sections to the always-on base prompt
    (company extraction + B2B lead-score signals).
    """
    ids = resolve_profile_ids(profile_ids)
    sections = [base_prompt.rstrip()]
    if ids:
        sections.append(
            "\nCONFIGURABLE SCORECARDS (additive — separate from B2B lead scoring):\n"
            "Do NOT output lead_score or status_tag — code computes those from your signals.\n"
            "Do NOT invent aggregate tiers — code may derive them from dimension scores.\n"
        )
        for pid in ids:
            profile = get_profile(pid)
            if profile:
                sections.append(build_profile_prompt_section(profile))
        sections.append(
            "CRITICAL (profiles):\n"
            "- Every selected profile MUST appear under profile_scores.\n"
            "- Dimension scores MUST be integers 1-10; confidence MUST be high|medium|low.\n"
            "- Reasons MUST cite specific scraped phrases when available.\n"
        )
    return "\n".join(sections)


def empty_dimension() -> Dict[str, Any]:
    return {
        "score": 1,
        "confidence": "low",
        "reason": "limited evidence available from homepage content",
    }


def empty_profile_scores(profile_ids: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    ids = resolve_profile_ids(profile_ids)
    out: Dict[str, Any] = {}
    for pid in ids:
        profile = get_profile(pid)
        if not profile:
            continue
        dims = {
            d["id"]: empty_dimension()
            for d in (profile.get("dimensions") or [])
        }
        out[pid] = {"dimensions": dims}
    return out


def _parse_dimension(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return empty_dimension()
    reason = str(raw.get("reason") or "").strip()
    if not reason:
        reason = "limited evidence available from homepage content"
    return {
        "score": _clamp_score_1_10(raw.get("score"), default=1),
        "confidence": _normalize_confidence(raw.get("confidence"), default="low"),
        "reason": reason,
    }


def normalize_profile_scores(
    analysis: Dict[str, Any],
    profile_ids: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """
    Normalize profile_scores on an analysis dict and apply legacy field aliases
    (e.g. ai_maturity_score) when the AI & Automation profile is active.
    Also computes optional average-tier aggregates.
    Returns the profile_scores dict (also written onto analysis).
    """
    ids = resolve_profile_ids(profile_ids)
    raw_scores = analysis.get("profile_scores")
    if not isinstance(raw_scores, dict):
        raw_scores = {}

    normalized: Dict[str, Any] = {}
    for pid in ids:
        profile = get_profile(pid)
        if not profile:
            continue
        block = raw_scores.get(pid)
        if not isinstance(block, dict):
            # Backward compat: pull legacy top-level fields into the AI profile
            block = {"dimensions": {}}
            aliases = profile.get("legacy_aliases") or {}
            for dim_id, mapping in aliases.items():
                block["dimensions"][dim_id] = {
                    "score": analysis.get(mapping.get("score")),
                    "reason": analysis.get(mapping.get("reason")),
                    "confidence": analysis.get(mapping.get("confidence")),
                }

        dims_raw = block.get("dimensions") if isinstance(block, dict) else {}
        if not isinstance(dims_raw, dict):
            dims_raw = {}

        dims_out: Dict[str, Any] = {}
        scores_for_avg: List[int] = []
        for dim in profile.get("dimensions") or []:
            dim_id = dim["id"]
            parsed = _parse_dimension(dims_raw.get(dim_id))
            dims_out[dim_id] = {
                **parsed,
                "name": dim.get("name") or dim_id,
            }
            scores_for_avg.append(parsed["score"])

            # Mirror legacy top-level keys when configured
            aliases = (profile.get("legacy_aliases") or {}).get(dim_id) or {}
            if aliases.get("score"):
                analysis[aliases["score"]] = parsed["score"]
            if aliases.get("reason"):
                analysis[aliases["reason"]] = parsed["reason"]
            if aliases.get("confidence"):
                analysis[aliases["confidence"]] = parsed["confidence"]

        entry: Dict[str, Any] = {
            "id": pid,
            "name": profile["name"],
            "short_name": profile.get("short_name") or profile["name"],
            "dimensions": dims_out,
        }

        agg = profile.get("aggregate") or {}
        if agg.get("type") == "average_tier" and scores_for_avg:
            avg = sum(scores_for_avg) / float(len(scores_for_avg))
            high_at = float(agg.get("high_at") or 7)
            medium_at = float(agg.get("medium_at") or 4)
            if avg >= high_at:
                tier = "High"
            elif avg >= medium_at:
                tier = "Medium"
            else:
                tier = "Low"
            entry["tier"] = tier
            entry["avg"] = round(avg, 2)
            if agg.get("tier_field"):
                analysis[agg["tier_field"]] = tier
            if agg.get("avg_field"):
                analysis[agg["avg_field"]] = round(avg, 2)

        normalized[pid] = entry

    analysis["profile_scores"] = normalized
    analysis["scoring_profile_ids"] = ids
    return normalized


def apply_heuristic_profile_scores(
    analysis: Dict[str, Any],
    homepage_text: str,
    profile_ids: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Keyword heuristics when NVIDIA is unavailable — fills profile_scores."""
    low = (homepage_text or "").lower()
    ids = resolve_profile_ids(profile_ids)
    scores: Dict[str, Any] = {}

    keyword_sets: Dict[str, Tuple[Tuple[str, ...], str]] = {
        "ai_maturity": (
            ("artificial intelligence", " machine learning", " ai ", "generative", "automation"),
            "AI/automation keywords",
        ),
        "transformation_readiness": (
            ("digital", "transformation", "cloud", "platform", "innovation", "api"),
            "digital/transformation keywords",
        ),
        "sustainability_initiatives": (
            ("sustainability", "esg", "net-zero", "net zero", "carbon", "climate", "iso 14001"),
            "sustainability/ESG keywords",
        ),
        "energy_transition": (
            ("renewable", "solar", "wind energy", "electrification", "green energy", "decarbon"),
            "energy-transition keywords",
        ),
        "saas_tooling": (
            ("saas", "software-as-a-service", "subscription", "cloud platform", "customer portal"),
            "SaaS/software keywords",
        ),
        "digital_operations": (
            ("api", "integration", "webhook", "online ordering", "e-commerce", "ecommerce"),
            "digital-ops keywords",
        ),
    }

    for pid in ids:
        profile = get_profile(pid)
        if not profile:
            continue
        dims: Dict[str, Any] = {}
        for dim in profile.get("dimensions") or []:
            dim_id = dim["id"]
            keys, label = keyword_sets.get(dim_id, ((), "relevant keywords"))
            hits = sum(1 for k in keys if k in low)
            score = 1 + min(6, hits * 2) if hits else 1
            conf = "medium" if hits >= 2 else ("low" if hits == 0 else "medium")
            if hits:
                reason = (
                    f"Heuristic: detected {hits} {label} on homepage "
                    "(NVIDIA unavailable — add NVIDIA_API_KEY for full scoring)."
                )
            else:
                reason = (
                    "limited evidence available from homepage content "
                    "(heuristic fallback; NVIDIA unavailable)."
                )
            dims[dim_id] = {"score": score, "confidence": conf, "reason": reason}
        scores[pid] = {"dimensions": dims}

    analysis["profile_scores"] = scores
    return normalize_profile_scores(analysis, ids)


def airtable_fields_for_profiles(record: Dict[str, Any]) -> Dict[str, Any]:
    """
    Build Airtable column map for profile_scores.
    Columns: "{airtable_prefix} — {Dimension} Score/Reason/Confidence"
    plus optional "{airtable_prefix} Tier".
    """
    profile_scores = record.get("profile_scores")
    if not isinstance(profile_scores, dict):
        return {}

    field_map: Dict[str, Any] = {}
    for pid, block in profile_scores.items():
        profile = get_profile(pid)
        if not profile or not isinstance(block, dict):
            continue
        prefix = profile.get("airtable_prefix") or profile.get("short_name") or profile["name"]
        dims = block.get("dimensions") or {}
        dim_meta = {d["id"]: d for d in (profile.get("dimensions") or [])}
        for dim_id, vals in dims.items():
            if not isinstance(vals, dict):
                continue
            dim_name = (dim_meta.get(dim_id) or {}).get("name") or dim_id
            col_base = f"{prefix} — {dim_name}"
            if vals.get("score") is not None:
                field_map[f"{col_base} Score"] = vals["score"]
            if vals.get("reason"):
                field_map[f"{col_base} Reason"] = vals["reason"]
            conf = str(vals.get("confidence") or "").strip().lower()
            if conf in ("high", "medium", "low"):
                field_map[f"{col_base} Confidence"] = conf.capitalize()
        if block.get("tier"):
            field_map[f"{prefix} Tier"] = block["tier"]
        if block.get("avg") is not None:
            field_map[f"{prefix} Avg"] = block["avg"]
    return field_map


def profile_optional_airtable_keys() -> set:
    """All possible dynamic profile column names (for retry-without-optional)."""
    keys: set = set()
    for profile in load_scoring_config()["profiles"]:
        prefix = profile.get("airtable_prefix") or profile.get("short_name") or profile["name"]
        keys.add(f"{prefix} Tier")
        keys.add(f"{prefix} Avg")
        for dim in profile.get("dimensions") or []:
            col_base = f"{prefix} — {dim['name']}"
            keys.add(f"{col_base} Score")
            keys.add(f"{col_base} Reason")
            keys.add(f"{col_base} Confidence")
    return keys
