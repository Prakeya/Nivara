"""
Unit tests for PII redaction.

Prioritizes correctness of what gets redacted (and what doesn't) over raw
line coverage, per Task 8's brief: "prioritize testing the actual redaction
correctness over just line coverage."
"""

from backend.pii_redaction import redact_pii, redact_dict, redact_log_message


class TestRedactPii:
    def test_redacts_email(self):
        text = "Contact me at jane.doe@example.com for details."
        result = redact_pii(text)
        assert "jane.doe@example.com" not in result
        assert "[REDACTED_EMAIL]" in result

    def test_redacts_ten_digit_phone(self):
        text = "Call 9876543210 now."
        result = redact_pii(text)
        assert "9876543210" not in result
        assert "[REDACTED_PHONE]" in result

    def test_redacts_pan(self):
        text = "PAN number is ABCDE1234F on file."
        result = redact_pii(text)
        assert "ABCDE1234F" not in result
        assert "[REDACTED_PAN]" in result

    def test_redacts_aadhaar(self):
        text = "Aadhaar: 1234 5678 9012"
        result = redact_pii(text)
        assert "1234 5678 9012" not in result
        assert "[REDACTED_AADHAAR]" in result

    def test_redacts_ifsc(self):
        text = "IFSC code HDFC0001234 for the branch."
        result = redact_pii(text)
        assert "HDFC0001234" not in result
        assert "[REDACTED_IFSC]" in result

    def test_redacts_long_bank_account_number(self):
        # 15 digits: too long to also match the 12-digit Aadhaar pattern.
        text = "Account number 123456789012345 was credited."
        result = redact_pii(text)
        assert "123456789012345" not in result
        assert "[REDACTED_ACCOUNT]" in result

    def test_non_string_input_returned_unchanged(self):
        assert redact_pii(12345) == 12345
        assert redact_pii(None) is None
        assert redact_pii({"a": 1}) == {"a": 1}

    def test_text_without_pii_is_unchanged(self):
        text = "The settlement was processed successfully."
        assert redact_pii(text) == text

    def test_multiple_pii_types_in_one_string_all_redacted(self):
        text = "Email jane@example.com, PAN ABCDE1234F, phone 9876543210."
        result = redact_pii(text)
        assert "jane@example.com" not in result
        assert "ABCDE1234F" not in result
        assert "9876543210" not in result
        assert "[REDACTED_EMAIL]" in result
        assert "[REDACTED_PAN]" in result
        assert "[REDACTED_PHONE]" in result


class TestRedactDict:
    def test_redacts_configured_fields_only(self):
        data = {
            "reason": "Contact jane@example.com for review",
            "settlement_id": "SETL_001",  # not a redacted field
        }
        result = redact_dict(data)
        assert "jane@example.com" not in result["reason"]
        assert result["settlement_id"] == "SETL_001"

    def test_default_fields_are_reason_explanation_reviewer_id_notes(self):
        data = {
            "reason": "reach 9876543210",
            "explanation": "PAN ABCDE1234F",
            "reviewer_id": "id 9876543210",
            "notes": "email a@b.com",
            "other_field": "9876543210 stays untouched",
        }
        result = redact_dict(data)
        assert "9876543210" not in result["reason"]
        assert "ABCDE1234F" not in result["explanation"]
        assert "9876543210" not in result["reviewer_id"]
        assert "a@b.com" not in result["notes"]
        # non-configured field is left as-is
        assert result["other_field"] == "9876543210 stays untouched"

    def test_custom_fields_argument_overrides_default(self):
        data = {"reason": "9876543210", "custom": "9876543210"}
        result = redact_dict(data, fields={"custom"})
        # "reason" is no longer in the redacted set
        assert result["reason"] == "9876543210"
        assert "9876543210" not in result["custom"]

    def test_redacts_nested_dict(self):
        data = {"nested": {"reason": "jane@example.com"}}
        result = redact_dict(data)
        assert "jane@example.com" not in result["nested"]["reason"]

    def test_redacts_list_of_dicts(self):
        data = {"items": [{"reason": "jane@example.com"}, {"reason": "clean"}]}
        result = redact_dict(data)
        assert "jane@example.com" not in result["items"][0]["reason"]
        assert result["items"][1]["reason"] == "clean"

    def test_redacts_list_of_strings_in_redacted_field(self):
        data = {"notes": ["email a@b.com", "no pii here"]}
        result = redact_dict(data)
        assert "a@b.com" not in result["notes"][0]
        assert result["notes"][1] == "no pii here"

    def test_non_string_non_dict_values_pass_through(self):
        data = {"reason": 42, "count": 7, "flag": True}
        result = redact_dict(data)
        assert result["reason"] == 42
        assert result["count"] == 7
        assert result["flag"] is True

    def test_does_not_mutate_input(self):
        data = {"reason": "jane@example.com"}
        redact_dict(data)
        assert data["reason"] == "jane@example.com"


class TestRedactLogMessage:
    def test_redacts_pii_in_log_message(self):
        msg = "User jane@example.com logged in from 9876543210"
        result = redact_log_message(msg)
        assert "jane@example.com" not in result
        assert "9876543210" not in result

    def test_plain_message_unchanged(self):
        msg = "Settlement processed"
        assert redact_log_message(msg) == msg
