# SPDX-FileCopyrightText: (C) ColdFront Authors
#
# SPDX-License-Identifier: AGPL-3.0-or-later

import importlib.util

from django.apps import AppConfig


class OtelConfig(AppConfig):
    name = "coldfront.plugins.otel"

    def ready(self):
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.dbapi import trace_integration
        from opentelemetry.instrumentation.django import DjangoInstrumentor
        from opentelemetry.instrumentation.logging import LoggingInstrumentor
        from opentelemetry.instrumentation.redis import RedisInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        trace.set_tracer_provider(TracerProvider(resource=Resource.create()))
        trace.get_tracer_provider().add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))

        DjangoInstrumentor().instrument()
        LoggingInstrumentor().instrument(inject_trace_context=True, enable_log_auto_instrumentation=False)
        RedisInstrumentor().instrument()

        if importlib.util.find_spec("MySQLdb") is not None:
            import MySQLdb

            trace_integration(
                MySQLdb,
                "connect",
                "mysql",
                connection_attributes={"database": "db", "host": "host", "user": "user", "port": "port"},
            )

        try:
            from ldap3.core.connection import Connection as LdapConnection
            from ldap3.core.connection import SyncStrategy as LdapSyncStrategy
        except ImportError:
            LdapConnection = None

        if (
            LdapConnection is not None
            and LdapConnection.search.__name__ == "search"
            and LdapSyncStrategy.open.__name__ == "open"
        ):
            _tracer = trace.get_tracer(__name__)
            _original_open = LdapSyncStrategy.open
            _original_search = LdapConnection.search

            def _traced_open(self, *args, **kwargs):
                with _tracer.start_as_current_span("ldap3.connect"):
                    return _original_open(self, *args, **kwargs)

            def _traced_search(self, *args, **kwargs):
                with _tracer.start_as_current_span("ldap3.search"):
                    return _original_search(self, *args, **kwargs)

            LdapSyncStrategy.open = _traced_open
            LdapConnection.search = _traced_search
