"""svc-ordenes · pago de reservaciones y pedidos de la boletera.

Endpoints:
  POST /ordenes           paga una reservación → 201 | 404 | 409 (ya pagada, vencida, cancelada)
  GET  /ordenes/{id}      un pedido con su evento y sus asientos
  GET  /health            lista para recibir tráfico (revisa la base): readinessProbe
  GET  /health/vivo       el proceso responde: livenessProbe
  GET  /metrics           métricas para Prometheus (HTTP + ventas)

Se arranca con:  uvicorn --factory app.main:crear_app
"""
import logging
import random
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, generate_latest
from psycopg import Error as ErrorBaseDatos
from pydantic import BaseModel, Field, field_validator

from .config import Config
from .observabilidad import MetricasHTTP, configurar_logs, configurar_trazas
from .repositorio import NoEncontrado, Rechazado, Repositorio

log = logging.getLogger("svc-ordenes")

RUTAS_SIN_LOG = ("/health", "/health/vivo", "/metrics")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# ── Modelos ──────────────────────────────────────────────────────────
class Pago(BaseModel):
    reservacion_id: UUID
    cliente_nombre: str = Field(min_length=2, max_length=120)
    cliente_email: str = Field(max_length=200)

    @field_validator("cliente_email")
    @classmethod
    def email_valido(cls, valor: str) -> str:
        if not EMAIL.match(valor):
            raise ValueError("Correo no válido")
        return valor.lower()


class AsientoVendido(BaseModel):
    id: int
    seccion: str
    fila: str
    numero: int
    precio: Decimal


class Orden(BaseModel):
    id: int
    evento_id: int
    evento: str
    artista: str
    recinto: str
    ciudad: str
    fecha: datetime
    reservacion_id: UUID | None
    cliente_nombre: str
    cliente_email: str
    cantidad: int
    total: Decimal
    estado: str
    creada_en: datetime
    asientos: list[AsientoVendido]


# ── Métricas de ventas ───────────────────────────────────────────────
class MetricasVentas:
    """Ventas hechas por este servicio desde que arrancó (contadores).

    La métrica de negocio "boletos vendidos por evento" completa (incluida la
    venta inicial) la da svc-eventos desde la base. Aquí se cuenta el ritmo
    de venta: rate(boletera_ventas_boletos_total[5m])."""

    def __init__(self, registro: CollectorRegistry):
        self.ordenes = Counter(
            "boletera_ordenes_total", "Órdenes pagadas", ["evento_id"], registry=registro,
        )
        self.boletos = Counter(
            "boletera_ventas_boletos_total", "Boletos vendidos por este servicio", ["evento_id", "seccion"], registry=registro,
        )
        self.ingresos = Counter(
            "boletera_ingresos_pesos_total", "Ingresos en pesos", ["evento_id"], registry=registro,
        )
        self.rechazos = Counter(
            "boletera_pagos_rechazados_total", "Pagos rechazados por motivo", ["motivo"], registry=registro,
        )


# ── Falla a propósito para la demo 3 ─────────────────────────────────
def inyectar_falla(request: Request) -> None:
    tasa = request.app.state.config.error_rate
    if tasa > 0 and random.random() < tasa:
        log.error("Falla inyectada para la demo", extra={"datos": {"error_rate": tasa, "ruta": request.url.path}})
        raise HTTPException(status_code=500, detail="Error inyectado (ERROR_RATE)")


def crear_app(config: Config | None = None, repositorio=None) -> FastAPI:
    config = config or Config.desde_entorno()
    configurar_logs(config.servicio, config.log_level)
    configurar_trazas(config.servicio, config.version, config.otlp_endpoint)
    repo = repositorio or Repositorio(config.conninfo, config.db_pool_max)

    registro = CollectorRegistry()
    metricas = MetricasHTTP(registro, config.servicio, config.version)
    ventas = MetricasVentas(registro)

    @asynccontextmanager
    async def ciclo_de_vida(app: FastAPI):
        repo.abrir()
        log.info("Servicio iniciado", extra={"datos": {"version": config.version, "db_host": config.db_host}})
        yield
        repo.cerrar()
        log.info("Servicio detenido")

    app = FastAPI(
        title="svc-ordenes",
        description="Pago de reservaciones y pedidos de la boletera",
        version=config.version,
        lifespan=ciclo_de_vida,
    )
    app.state.config = config

    # ── Middleware: métricas y log de cada petición ──────────────────
    @app.middleware("http")
    async def observar(request: Request, call_next):
        inicio = time.perf_counter()
        try:
            respuesta = await call_next(request)
            codigo = respuesta.status_code
        except Exception:
            codigo = 500
            log.exception("Error no controlado")
            respuesta = JSONResponse({"detail": "Error interno"}, status_code=500)
        segundos = time.perf_counter() - inicio
        ruta_ = request.scope.get("route")
        ruta = ruta_.path if ruta_ else "desconocida"
        if request.url.path != "/metrics":
            metricas.registrar(request.method, ruta, codigo, segundos)
        if request.url.path not in RUTAS_SIN_LOG or codigo >= 500:
            nivel = logging.ERROR if codigo >= 500 else logging.WARNING if codigo >= 400 else logging.INFO
            log.log(nivel, "%s %s %s", request.method, request.url.path, codigo, extra={"datos": {
                "metodo": request.method, "ruta": ruta, "path": request.url.path,
                "codigo": codigo, "duracion_ms": round(segundos * 1000, 1),
            }})
        return respuesta

    # ── Errores → códigos HTTP ───────────────────────────────────────
    @app.exception_handler(NoEncontrado)
    async def no_encontrado(request: Request, error: NoEncontrado):
        return JSONResponse({"detail": str(error)}, status_code=404)

    @app.exception_handler(Rechazado)
    async def rechazado(request: Request, error: Rechazado):
        cuerpo = {"detail": str(error), "motivo": error.motivo}
        if error.orden_id:
            cuerpo["orden_id"] = error.orden_id
        return JSONResponse(cuerpo, status_code=409)

    @app.exception_handler(ErrorBaseDatos)
    async def base_no_disponible(request: Request, error: Exception):
        log.error("Error de base de datos", exc_info=error, extra={"datos": {"path": request.url.path}})
        return JSONResponse({"detail": "Base de datos no disponible"}, status_code=503)

    # ── API ──────────────────────────────────────────────────────────
    api = APIRouter(dependencies=[Depends(inyectar_falla)])

    @api.post("/ordenes", response_model=Orden, status_code=201, summary="Paga una reservación")
    def pagar(datos: Pago):
        try:
            orden = repo.pagar(datos.reservacion_id, datos.cliente_nombre, datos.cliente_email)
        except Rechazado as error:
            ventas.rechazos.labels(motivo=error.motivo).inc()
            log.warning("Pago rechazado", extra={"datos": {
                "reservacion_id": str(datos.reservacion_id), "motivo": error.motivo,
            }})
            raise
        evento = str(orden["evento_id"])
        ventas.ordenes.labels(evento_id=evento).inc()
        ventas.ingresos.labels(evento_id=evento).inc(float(orden["total"]))
        for asiento in orden["asientos"]:
            ventas.boletos.labels(evento_id=evento, seccion=asiento["seccion"]).inc()
        log.info("Orden pagada", extra={"datos": {
            "orden_id": orden["id"], "evento_id": orden["evento_id"], "cantidad": orden["cantidad"],
            "total": str(orden["total"]), "reservacion_id": str(datos.reservacion_id),
        }})
        return orden

    @api.get("/ordenes/{orden_id}", response_model=Orden, summary="Un pedido con su evento y asientos")
    def obtener(orden_id: int):
        return repo.obtener(orden_id)

    app.include_router(api)

    # ── Salud y métricas ─────────────────────────────────────────────
    @app.get("/", include_in_schema=False)
    def raiz():
        return {"servicio": config.servicio, "version": config.version, "docs": "/docs"}

    @app.get("/health", summary="Listo para recibir tráfico (revisa la base)")
    def salud(respuesta: Response):
        try:
            repo.salud()
            return {"estado": "ok", "servicio": config.servicio, "version": config.version, "base_de_datos": "ok"}
        except Exception as error:
            log.warning("La base de datos no responde", extra={"datos": {"error": str(error)}})
            respuesta.status_code = 503
            return {"estado": "degradado", "servicio": config.servicio, "base_de_datos": "sin conexión"}

    @app.get("/health/vivo", summary="El proceso responde")
    def vivo():
        return {"estado": "ok"}

    @app.get("/metrics", include_in_schema=False)
    def exportar_metricas():
        return Response(generate_latest(registro), media_type=CONTENT_TYPE_LATEST)

    FastAPIInstrumentor.instrument_app(app, excluded_urls="health,metrics")
    return app
