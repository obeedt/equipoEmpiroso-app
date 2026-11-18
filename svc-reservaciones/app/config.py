"""Configuración del servicio, leída de variables de entorno.

En Kubernetes vienen del ConfigMap "svc-reservaciones-config" (repo GitOps,
tienda/svc-reservaciones/) y la contraseña de la base, del Secret
"postgres-app" (nunca de Git).
"""
import os
from dataclasses import dataclass

from psycopg.conninfo import make_conninfo


@dataclass(frozen=True)
class Config:
    servicio: str = "svc-reservaciones"
    version: str = "dev"
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "boletera"
    db_user: str = "boletera_app"
    db_password: str = ""
    db_pool_max: int = 5
    error_rate: float = 0.0          # 0 a 1: fracción de peticiones que fallan a propósito (demo 3)
    log_level: str = "INFO"
    otlp_endpoint: str = ""          # p. ej. http://jaeger-collector.logging:4318 ; vacío = no exportar
    reserva_minutos: int = 10        # cuánto tiempo quedan apartados los asientos
    liberador_segundos: int = 60     # cada cuánto se liberan las reservaciones vencidas (0 = nunca)

    @classmethod
    def desde_entorno(cls) -> "Config":
        return cls(
            servicio=os.getenv("OTEL_SERVICE_NAME", cls.servicio),
            version=os.getenv("APP_VERSION", cls.version),
            db_host=os.getenv("DB_HOST", cls.db_host),
            db_port=int(os.getenv("DB_PORT", cls.db_port)),
            db_name=os.getenv("DB_NAME", cls.db_name),
            db_user=os.getenv("DB_USER", cls.db_user),
            db_password=os.getenv("DB_PASSWORD", cls.db_password),
            db_pool_max=int(os.getenv("DB_POOL_MAX", cls.db_pool_max)),
            error_rate=float(os.getenv("ERROR_RATE", cls.error_rate)),
            log_level=os.getenv("LOG_LEVEL", cls.log_level),
            otlp_endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", cls.otlp_endpoint),
            reserva_minutos=int(os.getenv("RESERVA_MINUTOS", cls.reserva_minutos)),
            liberador_segundos=int(os.getenv("LIBERADOR_SEGUNDOS", cls.liberador_segundos)),
        )

    @property
    def conninfo(self) -> str:
        return make_conninfo(
            host=self.db_host,
            port=self.db_port,
            dbname=self.db_name,
            user=self.db_user,
            password=self.db_password,
            application_name=self.servicio,
            connect_timeout=3,
        )
