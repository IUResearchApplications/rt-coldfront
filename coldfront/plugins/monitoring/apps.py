from django.apps import AppConfig
from opentelemetry.instrumentation.mysqlclient import MySQLClientInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry import trace
from opentelemetry.instrumentation.django import DjangoInstrumentor


class MonitoringConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "coldfront.plugins.monitoring"

    def ready(self):
        trace.set_tracer_provider(TracerProvider(resource=Resource.create()))
        trace.get_tracer_provider().add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        MySQLClientInstrumentor().instrument()
        DjangoInstrumentor().instrument()
