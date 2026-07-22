from django.core.exceptions import ValidationError


def validate_query_data(value):
    """Validate that query_data is a valid JSON-serializable dict."""
    if value is not None and not isinstance(value, dict):
        raise ValidationError("query_data must be a JSON object (dict).")
