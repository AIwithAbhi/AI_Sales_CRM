#!/usr/bin/env python3
"""
Dual verification for insufficient-data / no-defaults + ambiguity cap.

Run A: process companies with the configured NVIDIA key (valid ⇒ scores;
       placeholder/401 ⇒ Not scored + Unknowns, never fake Cold/1-50).
Run B: force_analysis_fail on Siemens Energy → Not scored, Unknowns, zero points.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env", override=True)

from pipeline.analyzer import probe_nvidia_api
from services.lead_processing import process_company


COMPANIES_A = [
    "SRK",
    "Semines",
    "BrightPath Bakery",
    "DHL",
    "Siemens Energy",
    "Bosch",
    "Vestas",
]


def _unknown_fields(r: dict) -> list:
    fields = []
    for k in ("industry", "size_estimate", "b2b_evidence", "business_model"):
        v = str(r.get(k) or "").strip().lower()
        if v in ("", "unknown", "n/a", "none", "not stated on website"):
            fields.append(k)
    if r.get("b2b_buyer") is None:
        fields.append("b2b_buyer")
    if r.get("lead_score") is None or r.get("status_tag") == "Not scored":
        fields.append("lead_score")
    return fields


def _summarize(r: dict) -> dict:
    sb = r.get("score_breakdown") or {}
    return {
        "company": r.get("company_name"),
        "url": r.get("url") or "",
        "score": r.get("lead_score"),
        "status_tag": r.get("status_tag"),
        "scored": bool(r.get("scored")),
        "match_confidence": r.get("match_confidence"),
        "match_ambiguous": bool(r.get("match_ambiguous")),
        "entity_labels": r.get("entity_labels") or [],
        "selectable_candidates": [
            {
                "brand": c.get("brand"),
                "domain": c.get("domain"),
                "url": c.get("url"),
            }
            for c in (r.get("selectable_candidates") or [])[:5]
        ],
        "review_needed": bool(r.get("review_needed")),
        "analysis_failed": bool(r.get("analysis_failed")),
        "analysis_error": r.get("analysis_error") or "",
        "industry": r.get("industry"),
        "size_estimate": r.get("size_estimate"),
        "unknown_fields": _unknown_fields(r),
        "score_breakdown": {
            "industry_points": sb.get("industry_points", 0),
            "size_points": sb.get("size_points", 0),
            "b2b_points": sb.get("b2b_points", 0),
            "signal_points": sb.get("signal_points", 0),
            "raw_total": sb.get("raw_total", 0),
        },
        "score_reason": (r.get("score_reason") or "")[:200],
        "error": r.get("error"),
    }


def run_a() -> dict:
    nvidia = probe_nvidia_api()
    rows = []
    for name in COMPANIES_A:
        print(f"\n=== A: {name} ===", flush=True)
        r = process_company(name)
        s = _summarize(r)
        rows.append(s)
        score_label = (
            "Not scored" if s["score"] is None or s["status_tag"] == "Not scored"
            else s["score"]
        )
        print(
            f"  url={s['url'] or '—'} score={score_label} "
            f"conf={s['match_confidence']} amb={s['match_ambiguous']} "
            f"unknown={s['unknown_fields']}",
            flush=True,
        )
        if s["match_ambiguous"]:
            print(f"  entities={s['entity_labels']}", flush=True)
            for c in s["selectable_candidates"]:
                print(f"    cand: {c.get('brand')} → {c.get('url')}", flush=True)
    return {"nvidia_probe": nvidia, "results": rows}


def run_b() -> dict:
    print("\n=== B: Siemens Energy (force_analysis_fail / simulated 401) ===", flush=True)
    # Preselect homepage so ambiguity pause cannot skip the analysis-fail path
    r = process_company(
        "Siemens Energy",
        preselected_url="https://www.siemens-energy.com/us/en/home.html",
        force_analysis_fail=True,
    )
    s = _summarize(r)
    print(
        f"  url={s['url'] or '—'} score={s['score']} status={s['status_tag']} "
        f"unknown={s['unknown_fields']} breakdown={s['score_breakdown']} "
        f"err={s.get('analysis_error') or s.get('score_reason')}",
        flush=True,
    )
    proofs = {
        "not_scored": s["status_tag"] == "Not scored" and s["score"] is None,
        "unknown_industry": str(s["industry"]).lower() == "unknown",
        "unknown_size": str(s["size_estimate"]).lower() == "unknown",
        "zero_points": all(
            int(s["score_breakdown"].get(k, 0) or 0) == 0
            for k in (
                "industry_points",
                "size_points",
                "b2b_points",
                "signal_points",
                "raw_total",
            )
        ),
        "401_message": "401" in (s.get("analysis_error") or s.get("score_reason") or ""),
        "no_fake_cold": s["status_tag"] != "Cold",
        "no_fake_size_1_50": str(s["size_estimate"]) != "1-50",
    }
    return {"result": s, "proofs": proofs, "all_passed": all(proofs.values())}


def main() -> int:
    out = {
        "run_a": run_a(),
        "run_b": run_b(),
    }
    out_path = ROOT / "scripts" / "insufficient_data_verify_report.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}", flush=True)
    print(json.dumps({"run_b_proofs": out["run_b"]["proofs"], "nvidia": out["run_a"]["nvidia_probe"]}, indent=2))
    return 0 if out["run_b"]["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
