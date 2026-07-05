import functools
import io
import os
import time
from typing import Any, Callable, Dict, List, TypeVar

import pandas as pd

from backend.app.utils.logging import logger

T = TypeVar("T")


def parse_csv(file_content: bytes) -> List[str]:
    """
    Parse uploaded CSV bytes and extract company names.

    Auto-detects columns named 'company_name', 'company name' or uses the first column.
    
    Args:
        file_content: Raw bytes of the uploaded CSV.

    Returns:
        List of company names as strings, stripped and non-empty.
    """
    try:
        # Convert bytes to string stream for pandas
        df = pd.read_csv(io.StringIO(file_content.decode('utf-8')))

        # Try to find a column named 'company_name' (case-insensitive)
        column_name = None
        for col in df.columns:
            if col.lower().strip() in ["company_name", "company name"]:
                column_name = col
                break

        # If not found, use the first column
        if column_name is None:
            column_name = df.columns[0]
            logger.info(f"Using column '{column_name}' as company name source in CSV")

        # Extract values, strip whitespace, remove empty strings
        companies = df[column_name].astype(str).str.strip().tolist()
        companies = [c for c in companies if c and c.lower() not in ("nan", "none")]

        logger.info(f"Successfully parsed {len(companies)} companies from uploaded CSV")
        return companies

    except Exception as e:
        logger.error(f"Error parsing CSV bytes: {e}")
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
        max_attempts: Maximum number of retry attempts.
        delay: Delay in seconds between retries.
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
                    logger.warning(
                        f"Exception in '{func.__name__}' on attempt {attempt}/{max_attempts}: {e}. Retrying in {delay}s..."
                    )
                    if attempt < max_attempts:
                        time.sleep(delay)
                    else:
                        logger.error(f"All retries exhausted for '{func.__name__}': {last_exception}")
                        raise last_exception

            raise last_exception

        return wrapper

    return decorator


def load_headcount_data(path: str = "linkedin_headcount.csv") -> Dict[str, dict]:
    """
    Load LinkedIn headcount data from CSV and calculate growth metrics.

    Args:
        path: Path to the LinkedIn headcount CSV file.

    Returns:
        Dictionary keyed by company name (lowercase, stripped) containing:
        - headcount_week1: int
        - headcount_week4: int
        - growth_rate: float (percentage change from week1 to week4)
        - growth_label: str
    """
    try:
        # Check if the file is in root path
        # Since backend runs from backend/app directory, let's search in root
        if not os.path.exists(path):
            root_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), path)
            if os.path.exists(root_path):
                path = root_path
            else:
                parent_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), path)
                if os.path.exists(parent_path):
                    path = parent_path

        df = pd.read_csv(path, comment='#')

        headcount_data = {}

        for _, row in df.iterrows():
            company_name = str(row['company_name']).strip().lower()
            week1 = int(row.get('headcount_week1', 0))
            week4 = int(row.get('headcount_week4', 0))

            # Calculate growth rate
            if week1 > 0:
                growth_rate = ((week4 - week1) / week1) * 100
            else:
                growth_rate = 0.0

            # Determine growth label
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
                "growth_label": growth_label
            }

        logger.info(f"Loaded headcount trend data for {len(headcount_data)} companies successfully")
        return headcount_data

    except FileNotFoundError:
        logger.warning(f"Headcount CSV file not found at: {path}. Headcount data will not be available.")
        return {}
    except Exception as e:
        logger.error(f"Error loading headcount data: {e}")
        return {}
