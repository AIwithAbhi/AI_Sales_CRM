"""Test script for validation logic to prevent error JSON in Airtable."""

import sys
sys.path.insert(0, '.')

from pipeline.crm import validate_record


def test_valid_record():
    """Test that a valid record passes validation."""
    valid_record = {
        "company_name": "Test Company",
        "url": "https://test.com",
        "summary": "Test summary",
        "industry": "Technology",
        "size_estimate": "51-200 employees",
        "b2b_buyer": True,
        "lead_score": 8,
        "status_tag": "Hot",
        "score_reason": "Good match"
    }
    
    is_valid, error = validate_record(valid_record)
    assert is_valid == True, f"Valid record should pass: {error}"
    print("✓ Test 1 PASSED: Valid record passes validation")


def test_error_json_in_field():
    """Test that records with error JSON are rejected."""
    error_record = {
        "company_name": "Test Company",
        "url": "https://test.com",
        "summary": '{"state":"error","errorType":"APIError"}',
        "industry": "Technology",
        "size_estimate": "51-200 employees",
        "b2b_buyer": True,
        "lead_score": 8,
        "status_tag": "Hot",
        "score_reason": "Good match"
    }
    
    is_valid, error = validate_record(error_record)
    assert is_valid == False, f"Error JSON should be rejected: {error}"
    assert "error" in error.lower(), f"Error message should mention error: {error}"
    print("✓ Test 2 PASSED: Record with error JSON in field is rejected")


def test_error_dict_in_field():
    """Test that records with error dict are rejected."""
    error_record = {
        "company_name": "Test Company",
        "url": "https://test.com",
        "summary": "Test summary",
        "industry": "Technology",
        "size_estimate": "51-200 employees",
        "b2b_buyer": True,
        "lead_score": 8,
        "status_tag": "Hot",
        "score_reason": {"state": "error", "errorType": "APIError"}
    }
    
    is_valid, error = validate_record(error_record)
    assert is_valid == False, f"Error dict should be rejected: {error}"
    assert "error" in error.lower(), f"Error message should mention error: {error}"
    print("✓ Test 3 PASSED: Record with error dict in field is rejected")


def test_missing_required_field():
    """Test that records missing required fields are rejected."""
    invalid_record = {
        "company_name": "Test Company",
        "url": "https://test.com",
        # Missing summary
        "industry": "Technology",
        "size_estimate": "51-200 employees",
        "b2b_buyer": True,
        "lead_score": 8,
        "status_tag": "Hot",
        "score_reason": "Good match"
    }
    
    is_valid, error = validate_record(invalid_record)
    assert is_valid == False, f"Missing required field should be rejected: {error}"
    assert "summary" in error.lower(), f"Error message should mention missing field: {error}"
    print("✓ Test 4 PASSED: Record missing required field is rejected")


def test_empty_required_field():
    """Test that records with empty required fields are rejected."""
    invalid_record = {
        "company_name": "Test Company",
        "url": "https://test.com",
        "summary": "",  # Empty
        "industry": "Technology",
        "size_estimate": "51-200 employees",
        "b2b_buyer": True,
        "lead_score": 8,
        "status_tag": "Hot",
        "score_reason": "Good match"
    }
    
    is_valid, error = validate_record(invalid_record)
    assert is_valid == False, f"Empty required field should be rejected: {error}"
    assert "summary" in error.lower(), f"Error message should mention empty field: {error}"
    print("✓ Test 5 PASSED: Record with empty required field is rejected")


def test_not_found_field():
    """Test that records with 'Not Found' in required fields are rejected."""
    invalid_record = {
        "company_name": "Test Company",
        "url": "https://test.com",
        "summary": "Not Found",
        "industry": "Technology",
        "size_estimate": "51-200 employees",
        "b2b_buyer": True,
        "lead_score": 8,
        "status_tag": "Hot",
        "score_reason": "Good match"
    }
    
    is_valid, error = validate_record(invalid_record)
    assert is_valid == False, f"'Not Found' in field should be rejected: {error}"
    assert "summary" in error.lower(), f"Error message should mention field: {error}"
    print("✓ Test 6 PASSED: Record with 'Not Found' in field is rejected")


def test_traceback_indicator():
    """Test that records with 'traceback' indicator are rejected."""
    error_record = {
        "company_name": "Test Company",
        "url": "https://test.com",
        "summary": "Test summary",
        "industry": "Technology",
        "size_estimate": "51-200 employees",
        "b2b_buyer": True,
        "lead_score": 8,
        "status_tag": "Hot",
        "score_reason": "Good match",
        "extra_field": '{"traceback":"Error occurred"}'
    }
    
    is_valid, error = validate_record(error_record)
    assert is_valid == False, f"Record with traceback should be rejected: {error}"
    assert "traceback" in error.lower(), f"Error message should mention traceback: {error}"
    print("✓ Test 7 PASSED: Record with traceback indicator is rejected")


if __name__ == "__main__":
    print("Running validation logic tests...\n")
    
    try:
        test_valid_record()
        test_error_json_in_field()
        test_error_dict_in_field()
        test_missing_required_field()
        test_empty_required_field()
        test_not_found_field()
        test_traceback_indicator()
        
        print("\n" + "="*50)
        print("✅ ALL TESTS PASSED")
        print("="*50)
        print("\nValidation logic is working correctly:")
        print("- Valid records pass validation")
        print("- Records with error JSON are rejected")
        print("- Records with error indicators are rejected")
        print("- Records missing required fields are rejected")
        print("- Records with empty/Not Found fields are rejected")
        
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ UNEXPECTED ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
