"""svc-reservaciones · el carrito de la boletera (servicio distintivo del proyecto 3).

El usuario elige sección y cantidad; el servicio aparta los mejores lugares
disponibles por unos minutos (RESERVA_MINUTOS). Si no se pagan a tiempo, se
liberan solos.

Endpoints:
  POST   /reservaciones          aparta lugares → 201 | 404 evento | 409 sin lugares
  GET    /reservaciones/{id}     consulta una reservación con sus asientos
  DELETE /reservaciones/{id}     la cancela y libera sus asientos → 409 si ya se pagó
  GET    /health                 lista para recibir tráfico (revisa la base): readinessProbe
  GET    /health/vivo            el proceso responde: livenessProbe
  GET    /metrics                métricas para Prometheus

Se arranca con:  uvicorn --factory app.main:crear_app
"""
import asyncio
import logging
import random
import time
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, generate_latest
from psycopg import Error as ErrorBaseDatos
from pydantic import BaseModel, Field

from .config import Config
from .observabilidad import MetricasHTTP, configurar_logs, configurar_trazas
from .repositorio import Conflicto, NoEncontrado, Repositorio, SinLugares

log = logging.getLogger("svc-reservaciones")

RUTAS_SIN_LOG = ("/health", "/health/vivo", "/metrics")


# ── Modelos ──────────────────────────────────────────────────────────
class NuevaReservacion(BaseModel):
    evento_id: int = Field(gt=0)
    seccion: Literal["VIP", "Preferente", "General"]
    cantidad: int = Field(ge=1, le=10, description="Boletos (1 a 10)")
    sesion: str = Field(min_length=8, max_length=100, description="Identificador del carrito (cookie del frontend)")


class AsientoReservado(BaseModel):
    id: int
    seccion: str
    fila: str
    numero: int
    precio: Decimal


class Reservacion(BaseModel):
    id: UUID
    evento_id: int
    sesion: str
    estado: str
    seccion: str | None
    cantidad: int
    total: Decimal
    expira_en: datetime
    creada_en: datetime
    asientos: list[AsientoReservado]


# ── Métricas propias ─────────────────────────────────────────────────
class MetricasReservaciones:
    def __init__(self, registro: CollectorRegistry):
        self.intentos = Counter(
            "boletera_reservaciones_total", "Intentos de reservación por resultado",
            ["resultado"], registry=registro,           # creada | sin_lugares | evento_no_encontrado
        )
        self.boletos = Counter(
            "boletera_boletos_bloqueados_total", "Boletos apartados en reservaciones",
            ["evento_id", "seccion"], registry=registro,
        )
        self.canceladas = Counter(
            "boletera_reservaciones_canceladas_total", "Reservaciones canceladas por el usuario", registry=registro,
        )
        self.vencidas = Counter(
            "boletera_reservaciones_vencidas_total", "Reservaciones que vencieron sin pagarse", registry=registro,
        )


# ── Falla a propósito para la demo 3 ─────────────────────────────────
def inyectar_falla(request: Request) -> None:
    tasa = request.app.state.config.error_rate
    if tasa > 0 and random.random() < tasa:
        log.error("Falla inyectada para la demo", extra={"datos": {"error_rate": tasa, "ruta": request.url.path}})
        raise HTTPException(status_code=500, detail="Error inyectado (ERROR_RATE)")


async def liberador(repo, segundos: int, metricas: MetricasReservaciones) -> None:
    """Cada cierto tiempo, vence las reservaciones no pagadas y libera sus asientos."""
    while True:
        await asyncio.sleep(segundos)
        try:
            vencidas, liberados = await asyncio.to_thread(repo.liberar_vencidas)
            if vencidas:
                metricas.vencidas.inc(vencidas)
                log.info("Reservaciones vencidas liberadas",
                         extra={"datos": {"reservaciones": vencidas, "asientos": liberados}})
        except Exception:
            log.exception("No se pudieron liberar las reservaciones vencidas")


def crear_app(config: Config | None = None, repositorio=None) -> FastAPI:
    config = config or Config.desde_entorno()
    configurar_logs(config.servicio, config.log_level)
    configurar_trazas(config.servicio, config.version, config.otlp_endpoint)
    repo = repositorio or Repositorio(config.conninfo, config.db_pool_max)

    registro = CollectorRegistry()
    metricas = MetricasHTTP(registro, config.servicio, config.version)
    negocio = MetricasReservaciones(registro)

    @asynccontextmanager
    async def ciclo_de_vida(app: FastAPI):
        repo.abrir()
        tarea = None
        if config.liberador_segundos > 0:
            tarea = asyncio.create_task(liberador(repo, config.liberador_segundos, negocio))
        log.info("Servicio iniciado", extra={"datos": {
            "version": config.version, "db_host": config.db_host, "reserva_minutos": config.reserva_minutos,
        }})
        yield
        if tarea:
            tarea.cancel()
            with suppress(asyncio.CancelledError):
                await tarea
        repo.cerrar()
        log.info("Servicio detenido")

    app = FastAPI(
        title="svc-reservaciones",
        description="Carrito de la boletera: aparta lugares por unos minutos",
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

    @app.exception_handler(SinLugares)
    async def sin_lugares(request: Request, error: SinLugares):
        return JSONResponse({"detail": "No hay suficientes lugares en esa sección",
                             "disponibles": error.disponibles}, status_code=409)

    @app.exception_handler(Conflicto)
    async def conflicto(request: Request, error: Conflicto):
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.exception_handler(ErrorBaseDatos)
    async def base_no_disponible(request: Request, error: Exception):
        log.error("Error de base de datos", exc_info=error, extra={"datos": {"path": request.url.path}})
        return JSONResponse({"detail": "Base de datos no disponible"}, status_code=503)

    # ── API ──────────────────────────────────────────────────────────
    api = APIRouter(dependencies=[Depends(inyectar_falla)])

    @api.post("/reservaciones", response_model=Reservacion, status_code=201,
              summary="Aparta los mejores lugares disponibles de una sección")
    def crear_reservacion(datos: NuevaReservacion):
        try:
            reservacion = repo.crear(datos.evento_id, datos.seccion, datos.cantidad,
                                     datos.sesion, config.reserva_minutos)
        except SinLugares as error:
            negocio.intentos.labels(resultado="sin_lugares").inc()
            log.warning("Sin lugares suficientes", extra={"datos": {
                "evento_id": datos.evento_id, "seccion": datos.seccion,
                "pedidos": datos.cantidad, "disponibles": error.disponibles,
            }})
            raise
        except NoEncontrado:
            negocio.intentos.labels(resultado="evento_no_encontrado").inc()
            raise
        negocio.intentos.labels(resultado="creada").inc()
        negocio.boletos.labels(evento_id=str(datos.evento_id), seccion=datos.seccion).inc(datos.cantidad)
        log.info("Reservación creada", extra={"datos": {
            "reservacion_id": str(reservacion["id"]), "evento_id": datos.evento_id,
            "seccion": datos.seccion, "cantidad": datos.cantidad,
        }})
        return reservacion

    @api.get("/reservaciones/{reservacion_id}", response_model=Reservacion, summary="Consulta una reservación")
    def obtener_reservacion(reservacion_id: UUID):
        return repo.obtener(reservacion_id)

    @api.delete("/reservaciones/{reservacion_id}", summary="Cancela la reservación y libera sus asientos")
    def cancelar_reservacion(reservacion_id: UUID):
        liberados = repo.cancelar(reservacion_id)
        if liberados:
            negocio.canceladas.inc()
        log.info("Reservación cancelada", extra={"datos": {
            "reservacion_id": str(reservacion_id), "asientos_liberados": liberados,
        }})
        return {"reservacion_id": str(reservacion_id), "asientos_liberados": liberados}

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
