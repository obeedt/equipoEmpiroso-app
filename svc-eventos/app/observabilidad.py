"""Logs JSON con trace_id, trazas de OpenTelemetry y métricas HTTP.

Este archivo es igual en los 4 servicios de la boletera: así todos escriben
logs con el mismo formato y exponen las mismas métricas HTTP.
"""
import json
import logging
import os
import sys
from datetime import datetime, timezone

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram


# ── Logs ─────────────────────────────────────────────────────────────
class FormatoJSON(logging.Formatter):
    """Una línea JSON por log: nivel, servicio, mensaje y trace_id.

    Con el trace_id se pasa de un log de error a su traza en Jaeger.
    Los campos extra se pasan con  log.info("...", extra={"datos": {...}}).
    """

    def __init__(self, servicio: str):
        super().__init__()
        self.servicio = servicio

    def format(self, record: logging.LogRecord) -> str:
        contexto = trace.get_current_span().get_span_context()
        datos = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "nivel": record.levelname,
            "servicio": self.servicio,
            "logger": record.name,
            "mensaje": record.getMessage(),
            "trace_id": format(contexto.trace_id, "032x") if contexto.is_valid else None,
            "span_id": format(contexto.span_id, "016x") if contexto.is_valid else None,
        }
        extra = getattr(record, "datos", None)
        if isinstance(extra, dict):
            datos.update(extra)
        if record.exc_info:
            datos["error"] = self.formatException(record.exc_info)
        return json.dumps(datos, ensure_ascii=False, default=str)


def configurar_logs(servicio: str, nivel: str) -> None:
    salida = logging.StreamHandler(sys.stdout)
    salida.setFormatter(FormatoJSON(servicio))
    raiz = logging.getLogger()
    raiz.handlers[:] = [salida]
    raiz.setLevel(nivel.upper())
    # Uvicorn escribe por el mismo formato; su log de accesos se reemplaza
    # por el del middleware HTTP (que sí lleva trace_id).
    for nombre in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        registro = logging.getLogger(nombre)
        registro.handlers[:] = []
        registro.propagate = True
    logging.getLogger("uvicorn.access").disabled = True


# ── Trazas ───────────────────────────────────────────────────────────
_trazas_configuradas = False


def configurar_trazas(servicio: str, version: str, otlp_endpoint: str) -> None:
    """Proveedor de trazas de OpenTelemetry.

    - Con OTEL_EXPORTER_OTLP_ENDPOINT, envía las trazas por OTLP/HTTP (Jaeger
      o Tempo: cambiar de uno a otro solo cambia esa variable).
    - Con OTEL_TRACES_EXPORTER=console, las imprime (para pruebas locales).
    - Sin ninguna, igual genera trace_id para los logs, pero no las envía.
    """
    global _trazas_configuradas
    if _trazas_configuradas:
        return
    proveedor = TracerProvider(resource=Resource.create({
        "service.name": servicio,
        "service.namespace": "boletera",
        "service.version": version,
    }))
    if otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        proveedor.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    elif os.getenv("OTEL_TRACES_EXPORTER") == "console":
        proveedor.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(proveedor)
    _trazas_configuradas = True


# ── Métricas HTTP ────────────────────────────────────────────────────
class MetricasHTTP:
    """Las métricas mínimas que pide la rúbrica para cada servicio:
    peticiones por código HTTP y latencia (histograma)."""

    def __init__(self, registro: CollectorRegistry, servicio: str, version: str):
        self.peticiones = Counter(
            "http_requests_total", "Peticiones HTTP atendidas",
            ["method", "route", "status"], registry=registro,
        )
        self.duracion = Histogram(
            "http_request_duration_seconds", "Duración de las peticiones HTTP",
            ["method", "route"], registry=registro,
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5),
        )
        info = Gauge("boletera_info", "Versión del servicio", ["servicio", "version"], registry=registro)
        info.labels(servicio=servicio, version=version).set(1)

    def registrar(self, metodo: str, ruta: str, codigo: int, segundos: float) -> None:
        self.peticiones.labels(method=metodo, route=ruta, status=str(codigo)).inc()
        self.duracion.labels(method=metodo, route=ruta).observe(segundos)
