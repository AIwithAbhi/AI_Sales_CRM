"""CRM module for pushing searched company data to Airtable."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict

from pyairtable import Api

from utils.helpers import normalize_company_size
from utils.record_validation import apply_review_flag, validate_lead_record

logger = logging.getLogger(__name__)


def validate_record(record: Dict[str, Any]) -> tuple:
    """
    Legacy wrapper — prefer validate_lead_record for new code.

    Returns:
        (is_valid, error_message)
    """
    ok, errors = validate_lead_record(record)
    return ok, "; ".join(errors) if errors else ""


def push_to_airtable(record: Dict[str, Any]) -> bool:
    """
    Push a searched company record to Airtable after QA validation.

    Skips records flagged review_needed or that fail validate_lead_record.
    """
    try:
        normalized_record = dict(record)
        if not normalized_record.get("company_name"):
            normalized_record["company_name"] = (
                record.get("name") or record.get("company") or "Unknown Company"
            )
        if not normalized_record.get("url"):
            normalized_record["url"] = (
                record.get("website")
                or record.get("homepage")
                or record.get("contact_page")
                or ""
            )
        if not normalized_record.get("summary"):
            normalized_record["summary"] = ""
        if not normalized_record.get("industry"):
            normalized_record["industry"] = "Other"

        normalized_record["size_estimate"] = normalize_company_size(
            normalized_record.get("size_estimate", "")
        )
        normalized_record["b2b_buyer"] = bool(normalized_record.get("b2b_buyer", False))

        # Ensure score is int 1–10 when present
        try:
            score = int(normalized_record.get("lead_score") or 0)
        except (TypeError, ValueError):
            score = 0
        normalized_record["lead_score"] = score

        flagged = apply_review_flag(normalized_record)
        if flagged.get("review_needed"):
            logger.warning(
                "Skipping Airtable push for '%s' — review needed: %s",
                flagged.get("company_name"),
                flagged.get("validation_errors"),
            )
            print(
                f"[SKIP] Review needed for '{flagged.get('company_name')}': "
                f"{flagged.get('validation_errors')}"
            )
            return False

        is_valid, validation_error = validate_record(flagged)
        if not is_valid:
            print(
                f"[ERROR] VALIDATION FAILED for '{flagged.get('company_name')}': "
                f"{validation_error}"
            )
            return False

        record = flagged

        api_key = os.getenv("AIRTABLE_API_KEY")
        base_id = os.getenv("AIRTABLE_BASE_ID")
        table_name = os.getenv("AIRTABLE_TABLE_NAME", "Leads")

        if not api_key or not base_id:
            print("[ERROR] AIRTABLE_API_KEY or AIRTABLE_BASE_ID not set")
            return False

        api = Api(api_key)
        table = api.table(base_id, table_name)

        company_name = record.get("company_name", "").strip().lower()
        for existing in table.all():
            existing_name = str(
                existing.get("fields", {}).get("Name", "")
            ).strip().lower()
            if existing_name and existing_name == company_name:
                print(
                    f"[SKIP] Duplicate: {record.get('company_name')} already exists in Airtable"
                )
                return False

        signals = record.get("buying_signals") or []
        if isinstance(signals, list):
            signals_text = ", ".join(str(s) for s in signals)
        else:
            signals_text = str(signals)

        field_map = {
            "Name": record.get("company_name", "Unknown Company"),
            "Website": record.get("url", ""),
            "Industry": record.get("industry", ""),
            "Company Size": record.get("size_estimate", ""),
            "B2B Buyer": bool(record.get("b2b_buyer", False)),
            "Lead Score": record.get("lead_score"),
            "Status": record.get("status_tag", ""),
            "Score Reason": record.get("score_reason", ""),
        }
        # Optional columns — only include if non-empty (won't fail if missing in base)
        if signals_text:
            field_map["Buying Signals"] = signals_text
        if record.get("b2b_evidence"):
            field_map["B2B Evidence"] = record.get("b2b_evidence")
        if record.get("confidence"):
            field_map["Confidence"] = record.get("confidence")
        if record.get("business_model"):
            field_map["Business Model"] = record.get("business_model")

        airtable_record = {
            k: v
            for k, v in field_map.items()
            if v is not None and not (isinstance(v, str) and v.strip() == "")
        }

        # Drop optional fields that may not exist in the base
        optional_keys = {
            "Buying Signals", "B2B Evidence", "Confidence", "Business Model",
        }
        try:
            print(
                f"[*] Pushing {record.get('company_name')} -> columns: "
                f"{list(airtable_record.keys())}"
            )
            created = table.create(airtable_record, typecast=True)
        except Exception as first_err:
            # Retry without optional columns if schema lacks them
            slim = {k: v for k, v in airtable_record.items() if k not in optional_keys}
            logger.warning(
                "Airtable create failed (%s); retrying without optional fields",
                first_err,
            )
            created = table.create(slim, typecast=True)

        print(
            f"[OK] Created Airtable record for {record.get('company_name')} "
            f"(id={created.get('id')})"
        )
        return True

    except Exception as e:
        print(f"[ERROR] Airtable error for '{record.get('company_name')}': {e}")
        print(f"   Error type: {type(e).__name__}")
        print(f"   Record data: {json.dumps(record, indent=2)[:500]}...")
        import traceback

        print(f"   Traceback: {traceback.format_exc()}")
        return False


def fetch_from_airtable() -> list:
    """Fetch all records from Airtable CRM."""
    try:
        api_key = os.getenv("AIRTABLE_API_KEY")
        base_id = os.getenv("AIRTABLE_BASE_ID")
        table_name = os.getenv("AIRTABLE_TABLE_NAME", "Leads")

        if not api_key or not base_id:
            print("Error: AIRTABLE_API_KEY or AIRTABLE_BASE_ID not set")
            return []

        api = Api(api_key)
        table = api.table(base_id, table_name)
        records = table.all()

        result = []
        for record in records:
            fields = dict(record["fields"])
            fields.setdefault("company_name", fields.get("Name", ""))
            fields.setdefault("industry", fields.get("Industry", ""))
            if "lead_score" not in fields:
                fields["lead_score"] = fields.get("Lead Score")
            fields.setdefault("status_tag", fields.get("Status", ""))
            result.append(fields)

        print(f"[OK] Fetched {len(result)} records from Airtable")
        return result

    except Exception as e:
        print(f"[ERROR] Error fetching from Airtable: {e}")
        return []


def push_regulatory_sales_opportunity(opportunity: Dict[str, Any]) -> bool:
    """
    Push a regulatory sales opportunity as its own Airtable row (one field per column).

    Uses typecast so missing columns can be auto-created when the token allows it.
    """
    try:
        api_key = os.getenv("AIRTABLE_API_KEY")
        base_id = os.getenv("AIRTABLE_BASE_ID")
        table_name = (
            os.getenv("AIRTABLE_REGULATORY_TABLE_NAME")
            or os.getenv("AIRTABLE_TABLE_NAME", "Leads")
        )
        if not api_key or not base_id:
            print("[ERROR] AIRTABLE_API_KEY or AIRTABLE_BASE_ID not set")
            return False

        api = Api(api_key)
        table = api.table(base_id, table_name)

        company = str(opportunity.get("company_name") or "Unknown Company").strip()
        event = str(opportunity.get("regulatory_event") or "").strip()
        # Soft dedupe by Name + Regulatory Event
        for existing in table.all():
            fields = existing.get("fields") or {}
            if (
                str(fields.get("Name", "")).strip().lower() == company.lower()
                and str(fields.get("Regulatory Event", "")).strip().lower()
                == event.lower()
                and event
            ):
                print(f"[SKIP] Regulatory opportunity already in Airtable: {company}")
                return False

        field_map = {
            "Name": company,
            "Regulatory Event": event,
            "Regulatory Body": opportunity.get("regulatory_body") or "",
            "Regulatory Topic": opportunity.get("regulatory_topic") or "",
            "Problem Identified": opportunity.get("problem") or "",
            "Business Impact": opportunity.get("business_impact") or "",
            "Recommended Solution": opportunity.get("solution") or "",
            "Sales Opportunity": bool(opportunity.get("sales_opportunity")),
            "Contact Name": opportunity.get("contact_name") or "",
            "Contact Role": opportunity.get("contact_role") or "",
            "Public Email": opportunity.get("email") or "",
            "Email Source": opportunity.get("email_source_url")
            or opportunity.get("email_source_name")
            or "",
            "Email Confidence": opportunity.get("email_confidence") or "",
            "Email Subject": opportunity.get("recommended_subject") or "",
            "Email Draft": opportunity.get("email_body") or "",
            "Regulatory Source URL": opportunity.get("source_url") or "",
            "Status": "Sales Draft",
        }
        payload = {
            k: v
            for k, v in field_map.items()
            if v is not None and not (isinstance(v, str) and v.strip() == "")
        }

        optional = {
            "Regulatory Body",
            "Regulatory Topic",
            "Problem Identified",
            "Business Impact",
            "Recommended Solution",
            "Sales Opportunity",
            "Contact Name",
            "Contact Role",
            "Public Email",
            "Email Source",
            "Email Confidence",
            "Email Subject",
            "Email Draft",
            "Regulatory Source URL",
        }
        try:
            created = table.create(payload, typecast=True)
        except Exception as first_err:
            logger.warning(
                "Airtable regulatory create failed (%s); retrying without optional fields",
                first_err,
            )
            slim = {k: v for k, v in payload.items() if k not in optional}
            # Always keep the essential opportunity columns if present
            for keep in (
                "Name",
                "Regulatory Event",
                "Email Subject",
                "Email Draft",
                "Regulatory Source URL",
                "Status",
            ):
                if keep in payload:
                    slim[keep] = payload[keep]
            created = table.create(slim, typecast=True)

        print(
            f"[OK] Created regulatory sales opportunity for {company} "
            f"(id={created.get('id')})"
        )
        return True
    except Exception as e:
        print(f"[ERROR] Airtable regulatory opportunity push failed: {e}")
        return False
