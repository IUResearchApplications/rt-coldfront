# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Span filtering for the otel plugin."""

from opentelemetry.sdk.trace import SpanProcessor


class DropStatementSpanProcessor(SpanProcessor):
    """Drops DB spans whose statement contains an excluded substring.

    Used to keep high-frequency, low-value spans out of the exported trace
    stream: django-q's schedule polling and MySQL's per-connection setup.

    Statements are normalized (whitespace runs collapsed to single spaces)
    before matching, so patterns can be written as plain single-line SQL
    regardless of how Django formats the query.
    """

    def __init__(self, inner, excluded_statements):
        self._inner = inner
        self._excluded_statements = tuple(excluded_statements)

    def on_start(self, span, parent_context):
        pass

    def on_end(self, span):
        statement = span.attributes.get("db.statement") or ""
        normalized = " ".join(statement.split())
        if any(pattern in normalized for pattern in self._excluded_statements):
            return
        self._inner.on_end(span)

    def shutdown(self):
        self._inner.shutdown()

    def force_flush(self, timeout_millis=30000):
        return self._inner.force_flush(timeout_millis)
