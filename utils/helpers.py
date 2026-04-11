"""Helper utility functions for the CRM pipeline."""

import functools
import time
from typing import Any, Callable, List, TypeVar

import pandas as pd
import streamlit as st

# Type variable for generic decorator
T = TypeVar("T")


def parse_csv(uploaded_file: st.runtime.uploaded_file_manager.UploadedFile) -> List[str]:
    """
    Parse a CSV file uploaded via Streamlit and extract company names.

    Args:
        uploaded_file: Streamlit uploaded file object containing CSV data.
            Expected to have at least one column; will auto-detect column
            named 'company_name' or use the first column.

    Returns:
        List of company names as strings, with whitespace stripped and
        empty rows removed.
    """
    try:
        # Read CSV into pandas DataFrame
        df = pd.read_csv(uploaded_file)

        # Try to find a column named 'company_name' (case-insensitive)
        column_name = None
        for col in df.columns:
            if col.lower().strip() == "company_name":
                column_name = col
                break

        # If not found, use the first column
        if column_name is None:
            column_name = df.columns[0]

        # Extract values, strip whitespace, remove empty strings
        companies = df[column_name].astype(str).str.strip().tolist()
        companies = [c for c in companies if c and c.lower() != "nan"]

        return companies

    except Exception as e:
        st.error(f"Error parsing CSV: {e}")
        return []


def get_status_tag(lead_score: int) -> str:
    """
    Convert a numeric lead score to a status tag.

    Args:
        lead_score: Integer lead score between 0 and 10.

    Returns:
        Status string:
        - "Hot" for scores 8-10
        - "Warm" for scores 5-7
        - "Cold" for scores 1-4
        - "Unknown" for score 0
    """
    if lead_score >= 8:
        return "Hot"
    elif lead_score >= 5:
        return "Warm"
    elif lead_score >= 1:
        return "Cold"
    else:
        return "Unknown"


def retry(max_attempts: int = 2, delay: float = 2.0) -> Callable:
    """
    Decorator that retries a function on exception.

    Args:
        max_attempts: Maximum number of retry attempts (default: 2).
        delay: Delay in seconds between retries (default: 2.0).

    Returns:
        Decorated function with retry logic.

    Example:
        @retry(max_attempts=3, delay=1.5)
        def flaky_api_call():
            ...
    """

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
                        # All retries exhausted
                        raise last_exception

            # This should never be reached, but satisfies type checker
            raise last_exception

        return wrapper

    return decorator
