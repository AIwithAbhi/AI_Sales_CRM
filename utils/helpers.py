"""Helper utility functions for the CRM pipeline."""

import functools
import io
import logging
import time
from typing import Any, BinaryIO, Callable, List, TypeVar, Union

import pandas as pd

logger = logging.getLogger(__name__)

T = TypeVar("T")


def parse_csv(uploaded_file: Union[BinaryIO, io.BytesIO, Any]) -> List[str]:
    """
    Parse a CSV file and extract company names.

    Args:
        uploaded_file: File-like object with CSV data.

    Returns:
        List of company names.
    """
    try:
        df = pd.read_csv(uploaded_file)

        column_name = None
        for col in df.columns:
            if col.lower().strip() == "company_name":
                column_name = col
                break

        if column_name is None:
            column_name = df.columns[0]

        companies = df[column_name].astype(str).str.strip().tolist()
        return [c for c in companies if c and c.lower() != "nan"]

    except Exception as e:
        logger.error("Error parsing CSV: %s", e)
        return []


def get_status_tag(lead_score: int) -> str:
    """Map a 1–10 lead score to Hot / Warm / Cold / Unknown."""
    if lead_score >= 8:
        return "Hot"
    if lead_score >= 5:
        return "Warm"
    if lead_score >= 1:
        return "Cold"
    return "Unknown"


def retry(max_attempts: int = 2, delay: float = 2.0) -> Callable:
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            last_exception = None
            for attempt in range(max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt < max_attempts:
                        time.sleep(delay)
                    else:
                        raise last_exception
            raise last_exception

        return wrapper

    return decorator


def load_headcount_data(path: str = "linkedin_headcount.csv") -> dict:
    try:
        df = pd.read_csv(path, comment="#")
        headcount_data = {}

        for _, row in df.iterrows():
            company_name = str(row["company_name"]).strip().lower()
            week1 = int(row.get("headcount_week1", 0))
            week4 = int(row.get("headcount_week4", 0))

            if week1 > 0:
                growth_rate = ((week4 - week1) / week1) * 100
            else:
                growth_rate = 0

            if week1 == 0:
                growth_label = "No data"
            elif growth_rate >= 20:
                growth_label = "Rapid growth"
            elif growth_rate >= 5:
                growth_label = "Growing"
            elif growth_rate >= -5:
                growth_label = "Stable"
            else:
                growth_label = "Shrinking"

            headcount_data[company_name] = {
                "headcount_week1": week1,
                "headcount_week4": week4,
                "growth_rate": round(growth_rate, 1),
                "growth_label": growth_label,
            }

        return headcount_data

    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.warning("Could not load headcount data: %s", e)
        return {}


def normalize_company_size(size_str: str, headcount: int = None) -> str:
    """
    Normalize company size to: 1-50 | 51-200 | 201-500 | 501-1000 | 1001+.

    Prefer LinkedIn headcount when available; otherwise parse AI / legacy labels.
    """
    import re

    def from_count(n: int) -> str:
        if n >= 1001:
            return "1001+"
        if n >= 501:
            return "501-1000"
        if n >= 201:
            return "201-500"
        if n >= 51:
            return "51-200"
        return "1-50"

    if headcount is not None:
        try:
            return from_count(int(headcount))
        except (ValueError, TypeError):
            pass

    s = str(size_str or "").strip()
    exact = {"1-50", "51-200", "201-500", "501-1000", "1001+"}
    if s in exact:
        return s

    lower = s.lower()
    # Legacy Small / Medium / High
    if lower == "high":
        return "1001+"
    if lower == "medium":
        return "201-500"
    if lower == "small":
        return "1-50"

    # Common AI / old range strings
    if "1001" in lower or "1000+" in lower or "200+" == lower or "enterprise" in lower:
        if "200+" in lower and "1000" not in lower and "1001" not in lower:
            return "201-500"
        return "1001+"
    if "501" in lower or "500+" in lower:
        return "501-1000"
    if "201" in lower or "200+" in lower:
        return "201-500"
    if "51-200" in lower or "51–200" in lower:
        return "51-200"
    if "1-50" in lower or "1-10" in lower or "11-50" in lower:
        return "1-50"

    nums = [int(n) for n in re.findall(r"\d+", s)]
    if nums:
        return from_count(max(nums) if "+" in s else nums[0])

    # Unknown / missing — do NOT invent a small-company default
    lower_unknown = (
        "not stated",
        "unknown",
        "n/a",
        "none",
        "insufficient",
        "unavailable",
    )
    if not s or any(u in lower for u in lower_unknown):
        return "Unknown"

    return "Unknown"

