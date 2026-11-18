"""svc-eventos · catálogo de conciertos de la boletera.

Endpoints:
  GET /eventos              conciertos a la venta (filtros: ?ciudad= & ?genero=)
  GET /eventos/{id}         un concierto con sus secciones, precio y lugares disponibles
  GET /health               lista para recibir tráfico (revisa la base): readinessProbe
  GET /health/vivo          el proceso responde: livenessProbe
  GET /metrics              métricas para Prometheus (HTTP + negocio)

Se arranca con:  uvicorn --factory app.main:crear_app
"""
import logging
import random
import time
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest
from psycopg import Error as ErrorBaseDatos
from psycopg_pool import PoolTimeout
from pydantic import BaseModel

from .config import Config
from .metricas_negocio import MetricasNegocio
from .observabilidad import MetricasHTTP, configurar_logs, configurar_trazas
from .repositorio import Repositorio

log = logging.getLogger("svc-eventos")

RUTAS_SIN_LOG = ("/health", "/health/vivo", "/metrics")


# ── Modelos de respuesta (aparecen documentados en /docs) ────────────
class Seccion(BaseModel):
    seccion: str
    precio: Decimal
    total: int
    disponibles: int


class Evento(BaseModel):
    id: int
    nombre: str
    artista: str
    genero: str
    descripcion: str
    recinto: str
    ciudad: str
    fecha: datetime
    precio_base: Decimal
    capacidad: int
    disponibles: int
    porcentaje_vendido: Decimal


class DetalleEvento(Evento):
    secciones: list[Seccion]


# ── Falla a propósito para la demo 3 ─────────────────────────────────
def inyectar_falla(request: Request) -> None:
    """Con ERROR_RATE > 0 (ConfigMap), esa fracción de peticiones responde 500.
    Sirve para provocar la alerta de errores 5xx y seguir la falla en
    Grafana, en la traza y en el log."""
    tasa = request.app.state.config.error_rate
    if tasa > 0 and random.random() < tasa:
        log.error("Falla inyectada para la demo", extra={"datos": {"error_rate": tasa, "ruta": request.url.path}})
        raise HTTPException(status_code=500, detail="Error inyectado (ERROR_RATE)")


def crear_app(config: Config | None = None, repositorio=None) -> FastAPI:
    config = config or Config.desde_entorno()
    configurar_logs(config.servicio, config.log_level)
    configurar_trazas(config.servicio, config.version, config.otlp_endpoint)
    repo = repositorio or Repositorio(config.conninfo, config.db_pool_max)

    @asynccontextmanager
    async def ciclo_de_vida(app: FastAPI):
        repo.abrir()
        log.info("Servicio iniciado", extra={"datos": {"version": config.version, "db_host": config.db_host}})
        yield
        repo.cerrar()
        log.info("Servicio detenido")

    app = FastAPI(
        title="svc-eventos",
        description="Catálogo de conciertos de la boletera",
        version=config.version,
        lifespan=ciclo_de_vida,
    )
    app.state.config = config

    registro = CollectorRegistry()
    metricas = MetricasHTTP(registro, config.servicio, config.version)
    registro.register(MetricasNegocio(repo))

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
        ruta = ruta_.path if ruta_ else "desconocida"   # plantilla (/eventos/{evento_id}), no la URL real
        if request.url.path != "/metrics":
            metricas.registrar(request.method, ruta, codigo, segundos)
        if request.url.path not in RUTAS_SIN_LOG or codigo >= 500:
            nivel = logging.ERROR if codigo >= 500 else logging.WARNING if codigo >= 400 else logging.INFO
            log.log(nivel, "%s %s %s", request.method, request.url.path, codigo, extra={"datos": {
                "metodo": request.method, "ruta": ruta, "path": request.url.path,
                "codigo": codigo, "duracion_ms": round(segundos * 1000, 1),
            }})
        return respuesta

    # ── Errores de la base de datos → 503 ────────────────────────────
    @app.exception_handler(ErrorBaseDatos)
    @app.exception_handler(PoolTimeout)
    async def base_no_disponible(request: Request, error: Exception):
        log.error("Error de base de datos", exc_info=error, extra={"datos": {"path": request.url.path}})
        return JSONResponse({"detail": "Base de datos no disponible"}, status_code=503)

    # ── API ──────────────────────────────────────────────────────────
    api = APIRouter(dependencies=[Depends(inyectar_falla)])

    @api.get("/eventos", response_model=list[Evento], summary="Conciertos a la venta")
    def listar_eventos(ciudad: str | None = None, genero: str | None = None):
        return repo.listar_eventos(ciudad, genero)

    @api.get("/eventos/{evento_id}", response_model=DetalleEvento, summary="Un concierto con sus secciones")
    def obtener_evento(evento_id: int):
        evento = repo.obtener_evento(evento_id)
        if evento is None:
            raise HTTPException(status_code=404, detail="Evento no encontrado")
        return evento

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

    # Al final: el middleware de OpenTelemetry queda por fuera de todo, así
    # cada petición tiene su traza y los logs de arriba llevan su trace_id.
    FastAPIInstrumentor.instrument_app(app, excluded_urls="health,metrics")
    return app
