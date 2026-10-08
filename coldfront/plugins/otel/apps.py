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
        from opentelemetry.instrumentation.redis import RedisInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        trace.set_tracer_provider(TracerProvider(resource=Resource.create()))
        trace.get_tracer_provider().add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))

        DjangoInstrumentor().instrument()
        RedisInstrumentor().instrument()

        if importlib.util.find_spec("MySQLdb") is not None:
            import MySQLdb

            trace_integration(
                MySQLdb,
                "connect",
                "mysql",
                connection_attributes={"database": "db", "host": "host", "user": "user", "port": "port"},
            )
