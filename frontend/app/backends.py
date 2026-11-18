"""Clientes HTTP para los tres servicios de la boletera.

Cada cliente está instrumentado con OpenTelemetry: la llamada aparece como un
tramo de la traza y lleva el encabezado "traceparent", así el servicio que la
recibe continúa la MISMA traza (frontend → svc-ordenes → PostgreSQL).
"""
import logging

import httpx
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

log = logging.getLogger("backends")


class ServicioNoDisponible(Exception):
    """El servicio no respondió, tardó demasiado o respondió con un error 5xx."""

    def __init__(self, servicio: str, detalle: str):
        super().__init__(f"{servicio}: {detalle}")
        self.servicio = servicio
        self.detalle = detalle


class Backends:
    def __init__(self, config, metricas, transporte: httpx.BaseTransport | None = None):
        self.metricas = metricas
        self.clientes = {}
        for nombre, url in (("svc-eventos", config.eventos_url),
                            ("svc-reservaciones", config.reservaciones_url),
                            ("svc-ordenes", config.ordenes_url)):
            cliente = httpx.Client(base_url=url, timeout=config.timeout_segundos, transport=transporte)
            HTTPXClientInstrumentor.instrument_client(cliente)
            self.clientes[nombre] = cliente

    def cerrar(self) -> None:
        for cliente in self.clientes.values():
            cliente.close()

    def llamar(self, servicio: str, metodo: str, ruta: str, **kwargs) -> httpx.Response:
        """Hace la petición. Regresa la respuesta si es 2xx o 4xx (las decide
        quien llama); lanza ServicioNoDisponible si falla la red o hay un 5xx."""
        try:
            respuesta = self.clientes[servicio].request(metodo, ruta, **kwargs)
        except httpx.HTTPError as error:
            self.metricas.labels(servicio=servicio, resultado="sin_respuesta").inc()
            raise ServicioNoDisponible(servicio, type(error).__name__) from error
        if respuesta.status_code >= 500:
            self.metricas.labels(servicio=servicio, resultado="error_5xx").inc()
            raise ServicioNoDisponible(servicio, f"respondió {respuesta.status_code}")
        self.metricas.labels(servicio=servicio, resultado="ok").inc()
        return respuesta
