# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Redaction of secrets in URL span attributes.

Query strings can carry credentials (OIDC authorization codes, CSRF state
tokens, etc.). The OpenTelemetry Django instrumentation records the raw
request target in http.target without redacting those values, so a
request_hook scrubs them before the span is exported.
"""

from urllib.parse import parse_qsl, urlencode

# Query parameters whose values must never be exported in spans.
SENSITIVE_QUERY_PARAMS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "auth",
        "code",
        "id_token",
        "password",
        "refresh_token",
        "secret",
        "session",
        "sessionid",
        "sig",
        "signature",
        "state",
        "token",
    }
)

_REDACTED = "REDACTED"

# Attributes that can carry a query string, across old and stable HTTP
# semantic conventions.
_URL_ATTRIBUTES = ("http.target", "http.url", "url.full", "url.query")


def redact_query_params(query):
    """Redact sensitive parameter values in a raw query string."""
    params = parse_qsl(query, keep_blank_values=True)
    if not params:
        return query
    return urlencode([(key, _REDACTED if key.lower() in SENSITIVE_QUERY_PARAMS else value) for key, value in params])


def redact_url(url):
    """Redact sensitive parameter values in a URL or request target."""
    base, separator, query = url.partition("?")
    if not separator:
        return url
    return f"{base}?{redact_query_params(query)}"


def redact_request_hook(span, request):
    """DjangoInstrumentor request_hook that scrubs URL span attributes."""
    if span is None or not span.is_recording():
        return
    attributes = getattr(span, "attributes", None) or {}
    for name in _URL_ATTRIBUTES:
        value = attributes.get(name)
        if not value:
            continue
        if name == "url.query":
            span.set_attribute(name, redact_query_params(value))
        else:
            span.set_attribute(name, redact_url(value))
