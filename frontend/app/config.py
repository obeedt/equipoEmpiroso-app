"""Configuración del frontend, leída de variables de entorno.

En Kubernetes vienen del ConfigMap "frontend-config" (repo GitOps,
tienda/frontend/). El frontend no usa la base de datos: todo lo pide a los
otros tres servicios por HTTP.
"""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    servicio: str = "frontend"
    version: str = "dev"
    eventos_url: str = "http://svc-eventos"
    reservaciones_url: str = "http://svc-reservaciones"
    ordenes_url: str = "http://svc-ordenes"
    timeout_segundos: float = 5.0
    error_rate: float = 0.0          # 0 a 1: fracción de páginas que fallan a propósito (demo 3)
    log_level: str = "INFO"
    otlp_endpoint: str = ""          # p. ej. http://jaeger-collector.logging:4318 ; vacío = no exportar
    banner: str = ""                 # aviso arriba de todas las páginas (vacío = sin aviso)

    @classmethod
    def desde_entorno(cls) -> "Config":
        return cls(
            servicio=os.getenv("OTEL_SERVICE_NAME", cls.servicio),
            version=os.getenv("APP_VERSION", cls.version),
            eventos_url=os.getenv("EVENTOS_URL", cls.eventos_url),
            reservaciones_url=os.getenv("RESERVACIONES_URL", cls.reservaciones_url),
            ordenes_url=os.getenv("ORDENES_URL", cls.ordenes_url),
            timeout_segundos=float(os.getenv("TIMEOUT_SEGUNDOS", cls.timeout_segundos)),
            error_rate=float(os.getenv("ERROR_RATE", cls.error_rate)),
            log_level=os.getenv("LOG_LEVEL", cls.log_level),
            otlp_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", cls.otlp_endpoint),
            banner=os.getenv("BANNER", cls.banner),
        )
