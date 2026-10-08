#!/usr/bin/env python3
"""Offline unit tests for match-quality fixes (no API keys required)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from utils.url_validation import domain_brand_matches_company
from pipeline.search import (
    _corporate_homepage_score,
    _is_service_portal_url,
    _detect_entity_ambiguity,
    _infer_typo_suggestion,
    _names_close,
)


def test_domain_brand_gate() -> None:
    assert domain_brand_matches_company("https://www.dhl.com/", "DHL")
    assert domain_brand_matches_company("https://dhl.com/", "DHL")
    assert domain_brand_matches_company("https://group.dhl.com/", "DHL")
    assert not domain_brand_matches_company("https://www.dhlexported.com/", "DHL")
    assert not domain_brand_matches_company("https://www.dhlsameday.com/", "DHL")
    assert domain_brand_matches_company("https://www.siemens.com/", "Siemens Energy")
    assert domain_brand_matches_company("https://www.siemens-energy.com/", "Siemens Energy")
    print("PASS domain_brand_gate")


def test_portal_detection_and_scores() -> None:
    portal = "https://mydhl.express.dhl/us/en/home.html"
    apex = "https://www.dhl.com/"
    lookalike = "https://www.dhlexported.com/"
    assert _is_service_portal_url(portal)
    assert not _is_service_portal_url(apex)
    assert _corporate_homepage_score(apex, "DHL") > 100
    assert _corporate_homepage_score(portal, "DHL") < 0
    assert _corporate_homepage_score(lookalike, "DHL") < 0
    assert _corporate_homepage_score(apex, "DHL") > _corporate_homepage_score(portal, "DHL")
    print("PASS portal_detection_and_scores")
    print(
        f"  DHL candidates: apex={_corporate_homepage_score(apex,'DHL')} "
        f"portal={_corporate_homepage_score(portal,'DHL')} "
        f"lookalike={_corporate_homepage_score(lookalike,'DHL')}"
    )


def test_brightpath_not_ambiguous() -> None:
    hits = [
        {"url": "https://brightpathkids.com/us", "title": "BrightPath Kids", "snippet": "childcare", "excluded": False},
        {"url": "https://www.busybeesna.com/our-brands", "title": "Our Brands", "snippet": "Busy Bees", "excluded": False},
    ]
    amb, labels, sel = _detect_entity_ambiguity(hits, "BrightPath Bakery")
    assert not amb, labels
    print("PASS brightpath_not_ambiguous", labels)


def test_srk_ambiguous() -> None:
    hits = [
        {"url": "https://srk.one/", "title": "SRK Diamonds", "snippet": "jewelry SRK", "excluded": False},
        {"url": "https://teamshahrukhkhan.com/", "title": "Shah Rukh Khan (SRK)", "snippet": "Official SRK", "excluded": False},
    ]
    amb, labels, sel = _detect_entity_ambiguity(hits, "SRK")
    assert amb and len(sel) >= 2
    print("PASS srk_ambiguous", labels)


def test_semines_typo() -> None:
    assert _names_close("Semines", "siemens")
    hits = [
        {"url": "https://www.siemens.com/en-us/", "title": "Siemens", "snippet": "Siemens AG", "excluded": False},
        {"url": "https://www.siemens.com/en-us/products/", "title": "Siemens Products", "snippet": "", "excluded": False},
        {"url": "https://jobs.siemens.com/", "title": "Jobs at Siemens", "snippet": "", "excluded": False},
        {"url": "https://www.siemens-energy.com/", "title": "Siemens Energy", "snippet": "", "excluded": False},
    ]
    amb, _, _ = _detect_entity_ambiguity(hits, "Semines")
    assert not amb
    typo = _infer_typo_suggestion("Semines", hits)
    assert typo and typo["suggested_name"] == "Siemens"
    assert "Did you mean Siemens?" in typo["reason"]
    print("PASS semines_typo", typo["reason"])


def test_ikea_nestle_not_ambiguous() -> None:
    for name, urls in [
        ("IKEA", ["https://www.ikea.com/", "https://www.ikea.com/us/en/"]),
        ("Nestle", ["https://www.nestle.com/", "https://www.nestle.com/aboutus"]),
        ("Revolut", ["https://www.revolut.com/", "https://www.revolut.com/business/"]),
    ]:
        hits = [{"url": u, "title": name, "snippet": name, "excluded": False} for u in urls]
        amb, labels, _ = _detect_entity_ambiguity(hits, name)
        assert not amb, (name, labels)
    print("PASS ikea_nestle_revolut_not_ambiguous")


def test_push_filter_mixed_batch() -> None:
    """Server-side validity rules for mixed batch."""
    rows = [
        {"company_name": "A", "error": None, "review_needed": False, "scored": True, "lead_score": 8, "status_tag": "Hot"},
        {"company_name": "B", "error": None, "review_needed": False, "scored": True, "lead_score": 6, "status_tag": "Warm"},
        {"company_name": "C", "error": "No confident match", "review_needed": True, "scored": False, "lead_score": None, "status_tag": "Error"},
        {"company_name": "D", "error": None, "review_needed": True, "scored": False, "lead_score": None, "status_tag": "Needs review", "match_ambiguous": True},
    ]

    def valid(r):
        return (
            not r.get("review_needed")
            and not r.get("match_ambiguous")
            and r.get("scored") is not False
            and r.get("lead_score") is not None
            and r.get("status_tag") not in ("Not scored", "Needs review", "Error", "Unknown")
            and not r.get("error")
        )

    pushable = [r for r in rows if valid(r)]
    assert len(pushable) == 2
    assert {r["company_name"] for r in pushable} == {"A", "B"}
    print("PASS push_filter_mixed_batch")


def main() -> int:
    test_domain_brand_gate()
    test_portal_detection_and_scores()
    test_brightpath_not_ambiguous()
    test_srk_ambiguous()
    test_semines_typo()
    test_ikea_nestle_not_ambiguous()
    test_push_filter_mixed_batch()
    print("\nAll offline match-quality unit tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
