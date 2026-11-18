"""frontend · el sitio web de la boletera.

Páginas (HTML generado en el servidor con Jinja2):
  GET  /                   catálogo de conciertos (filtros: ?ciudad= & ?genero=)
  GET  /evento/{id}        un concierto con sus secciones; formulario "agregar al carrito"
  POST /carrito            aparta lugares (svc-reservaciones) y lleva al carrito
  GET  /carrito            la reservación actual con su cuenta regresiva y el formulario de pago
  POST /carrito/cancelar   libera los lugares
  POST /pagar              paga la reservación (svc-ordenes) y lleva a la confirmación
  GET  /orden/{id}         confirmación del pedido
  GET  /health, /health/vivo, /metrics

El carrito vive en dos cookies: "sesion" (identifica el navegador) y
"reservacion" (el id de la reservación activa). No hay base de datos aquí.

Se arranca con:  uvicorn --factory app.main:crear_app
"""
import logging
import random
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, generate_latest

from .backends import Backends, ServicioNoDisponible
from .config import Config
from .observabilidad import MetricasHTTP, configurar_logs, configurar_trazas

log = logging.getLogger("frontend")

RUTAS_SIN_LOG = ("/health", "/health/vivo", "/metrics")
COOKIE_SESION = "sesion"
COOKIE_RESERVACION = "reservacion"

DIAS = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


# ── Formato para las plantillas ──────────────────────────────────────
def dinero(valor) -> str:
    return f"${Decimal(str(valor)):,.2f}"


def fecha(valor) -> str:
    """'2027-01-23T21:00:00-06:00' → 'sáb 23 ene 2027 · 21:00' (hora del recinto)."""
    f = datetime.fromisoformat(str(valor))
    return f"{DIAS[f.weekday()]} {f.day} {MESES[f.month - 1]} {f.year} · {f:%H:%M}"


def hora(valor) -> str:
    return f"{datetime.fromisoformat(str(valor)):%H:%M}"


def trace_id_actual() -> str | None:
    contexto = trace.get_current_span().get_span_context()
    return format(contexto.trace_id, "032x") if contexto.is_valid else None


def crear_app(config: Config | None = None, transporte=None) -> FastAPI:
    config = config or Config.desde_entorno()
    configurar_logs(config.servicio, config.log_level)
    configurar_trazas(config.servicio, config.version, config.otlp_endpoint)

    registro = CollectorRegistry()
    metricas = MetricasHTTP(registro, config.servicio, config.version)
    llamadas = Counter(
        "boletera_llamadas_backend_total", "Llamadas del frontend a los otros servicios",
        ["servicio", "resultado"], registry=registro,     # resultado: ok | error_5xx | sin_respuesta
    )
    backends = Backends(config, llamadas, transporte)

    plantillas = Jinja2Templates(directory=Path(__file__).parent / "plantillas")
    plantillas.env.filters.update(dinero=dinero, fecha=fecha, hora=hora)
    plantillas.env.globals.update(version=config.version, banner=config.banner)

    @asynccontextmanager
    async def ciclo_de_vida(app: FastAPI):
        log.info("Servicio iniciado", extra={"datos": {
            "version": config.version, "eventos": config.eventos_url,
            "reservaciones": config.reservaciones_url, "ordenes": config.ordenes_url,
        }})
        yield
        backends.cerrar()
        log.info("Servicio detenido")

    app = FastAPI(title="frontend", version=config.version, lifespan=ciclo_de_vida,
                  docs_url=None, redoc_url=None, openapi_url=None)

    def pagina(request: Request, nombre: str, contexto: dict | None = None, codigo: int = 200):
        return plantillas.TemplateResponse(request, nombre, contexto or {}, status_code=codigo)

    def falla_inyectada() -> None:
        if config.error_rate > 0 and random.random() < config.error_rate:
            log.error("Falla inyectada para la demo", extra={"datos": {"error_rate": config.error_rate}})
            raise ServicioNoDisponible("frontend", "error inyectado (ERROR_RATE)")

    # ── Middleware: métricas y log de cada petición ──────────────────
    @app.middleware("http")
    async def observar(request: Request, call_next):
        inicio = time.perf_counter()
        respuesta = await call_next(request)
        codigo = respuesta.status_code
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

    # ── Un servicio no responde → página de error con su folio ───────
    @app.exception_handler(ServicioNoDisponible)
    async def servicio_caido(request: Request, error: ServicioNoDisponible):
        folio = trace_id_actual()
        log.error("Servicio no disponible", extra={"datos": {
            "servicio_caido": error.servicio, "detalle": error.detalle, "path": request.url.path,
        }})
        return pagina(request, "error.html", {
            "titulo": "Algo salió mal",
            "mensaje": "Uno de nuestros servicios no responde en este momento. Intenta de nuevo en unos minutos.",
            "folio": folio,
        }, codigo=503)

    @app.exception_handler(HTTPException)
    async def no_encontrado(request: Request, error: HTTPException):
        return pagina(request, "error.html", {"titulo": "No encontrado", "mensaje": error.detail,
                                              "folio": None}, codigo=error.status_code)

    # ── Catálogo ─────────────────────────────────────────────────────
    @app.get("/", response_class=HTMLResponse)
    def catalogo(request: Request, ciudad: str | None = None, genero: str | None = None):
        falla_inyectada()
        eventos = backends.llamar("svc-eventos", "GET", "/eventos").json()
        ciudades = sorted({e["ciudad"] for e in eventos})
        generos = sorted({e["genero"] for e in eventos})
        visibles = [e for e in eventos if ciudad in (None, "", e["ciudad"]) and genero in (None, "", e["genero"])]
        return pagina(request, "catalogo.html", {
            "eventos": visibles, "ciudades": ciudades, "generos": generos,
            "ciudad": ciudad or "", "genero": genero or "",
        })

    @app.get("/evento/{evento_id}", response_class=HTMLResponse)
    def evento(request: Request, evento_id: int):
        falla_inyectada()
        respuesta = backends.llamar("svc-eventos", "GET", f"/eventos/{evento_id}")
        if respuesta.status_code == 404:
            raise HTTPException(404, "Ese concierto no existe o ya no está a la venta.")
        return pagina(request, "evento.html", {"evento": respuesta.json()})

    # ── Carrito ──────────────────────────────────────────────────────
    @app.post("/carrito")
    def agregar_al_carrito(request: Request, evento_id: int = Form(), seccion: str = Form(),
                           cantidad: int = Form()):
        falla_inyectada()
        sesion = request.cookies.get(COOKIE_SESION) or uuid.uuid4().hex
        # Si ya había un carrito, se libera antes de apartar lugares nuevos
        anterior = request.cookies.get(COOKIE_RESERVACION)
        if anterior:
            backends.llamar("svc-reservaciones", "DELETE", f"/reservaciones/{anterior}")
        respuesta = backends.llamar("svc-reservaciones", "POST", "/reservaciones", json={
            "evento_id": evento_id, "seccion": seccion, "cantidad": cantidad, "sesion": sesion,
        })
        if respuesta.status_code != 201:
            detalle = respuesta.json()
            if respuesta.status_code == 409:
                mensaje = (f"Ya no hay {cantidad} lugares juntos en {seccion}: "
                           f"quedan {detalle.get('disponibles', 0)}. Prueba con menos boletos u otra sección.")
            else:
                mensaje = "No se pudo apartar: revisa la sección y la cantidad."
            evento_ = backends.llamar("svc-eventos", "GET", f"/eventos/{evento_id}")
            if evento_.status_code == 404:
                raise HTTPException(404, "Ese concierto no existe o ya no está a la venta.")
            return pagina(request, "evento.html", {"evento": evento_.json(), "error": mensaje},
                          codigo=respuesta.status_code)
        reservacion = respuesta.json()
        log.info("Lugares apartados", extra={"datos": {
            "reservacion_id": reservacion["id"], "evento_id": evento_id,
            "seccion": seccion, "cantidad": cantidad,
        }})
        redireccion = RedirectResponse("/carrito", status_code=303)
        redireccion.set_cookie(COOKIE_SESION, sesion, max_age=30 * 24 * 3600, httponly=True, samesite="lax")
        redireccion.set_cookie(COOKIE_RESERVACION, reservacion["id"], max_age=3600, httponly=True, samesite="lax")
        return redireccion

    def reservacion_actual(request: Request) -> dict | None:
        reservacion_id = request.cookies.get(COOKIE_RESERVACION)
        if not reservacion_id:
            return None
        respuesta = backends.llamar("svc-reservaciones", "GET", f"/reservaciones/{reservacion_id}")
        if respuesta.status_code != 200:
            return None
        return respuesta.json()

    def ver_carrito(request: Request, error: str | None = None, codigo: int = 200,
                    nombre: str = "", email: str = ""):
        reservacion = reservacion_actual(request)
        evento_ = None
        if reservacion:
            evento_ = backends.llamar("svc-eventos", "GET", f"/eventos/{reservacion['evento_id']}").json()
        return pagina(request, "carrito.html", {
            "reservacion": reservacion, "evento": evento_, "error": error,
            "nombre": nombre, "email": email,
        }, codigo=codigo)

    @app.get("/carrito", response_class=HTMLResponse)
    def carrito(request: Request):
        falla_inyectada()
        return ver_carrito(request)

    @app.post("/carrito/cancelar")
    def cancelar(request: Request):
        reservacion_id = request.cookies.get(COOKIE_RESERVACION)
        if reservacion_id:
            backends.llamar("svc-reservaciones", "DELETE", f"/reservaciones/{reservacion_id}")
            log.info("Carrito cancelado", extra={"datos": {"reservacion_id": reservacion_id}})
        redireccion = RedirectResponse("/", status_code=303)
        redireccion.delete_cookie(COOKIE_RESERVACION)
        return redireccion

    # ── Pago ─────────────────────────────────────────────────────────
    @app.post("/pagar")
    def pagar(request: Request, cliente_nombre: str = Form(), cliente_email: str = Form()):
        falla_inyectada()
        reservacion_id = request.cookies.get(COOKIE_RESERVACION)
        if not reservacion_id:
            return RedirectResponse("/carrito", status_code=303)
        respuesta = backends.llamar("svc-ordenes", "POST", "/ordenes", json={
            "reservacion_id": reservacion_id, "cliente_nombre": cliente_nombre, "cliente_email": cliente_email,
        })
        if respuesta.status_code == 201:
            orden = respuesta.json()
            log.info("Compra completada", extra={"datos": {
                "orden_id": orden["id"], "evento_id": orden["evento_id"],
                "cantidad": orden["cantidad"], "total": orden["total"],
            }})
            redireccion = RedirectResponse(f"/orden/{orden['id']}", status_code=303)
            redireccion.delete_cookie(COOKIE_RESERVACION)
            return redireccion
        detalle = respuesta.json()
        if respuesta.status_code == 409 and detalle.get("motivo") == "ya_pagada" and detalle.get("orden_id"):
            return RedirectResponse(f"/orden/{detalle['orden_id']}", status_code=303)
        if respuesta.status_code == 422:
            return ver_carrito(request, "Revisa tu nombre y tu correo.", 422, cliente_nombre, cliente_email)
        mensajes = {
            "vencida": "Se acabó el tiempo para pagar y los lugares se liberaron. Elige tus boletos de nuevo.",
            "cancelada": "Este carrito se canceló. Elige tus boletos de nuevo.",
        }
        mensaje = mensajes.get(detalle.get("motivo"), "No se pudo completar el pago.")
        respuesta_html = ver_carrito(request, mensaje, respuesta.status_code)
        respuesta_html.delete_cookie(COOKIE_RESERVACION)
        return respuesta_html

    @app.get("/orden/{orden_id}", response_class=HTMLResponse)
    def orden(request: Request, orden_id: int):
        falla_inyectada()
        respuesta = backends.llamar("svc-ordenes", "GET", f"/ordenes/{orden_id}")
        if respuesta.status_code == 404:
            raise HTTPException(404, "No encontramos ese pedido.")
        return pagina(request, "orden.html", {"orden": respuesta.json()})

    # ── Salud y métricas ─────────────────────────────────────────────
    # /health no depende de los otros servicios: si uno se cae, el sitio
    # sigue arriba y muestra su página de error (con folio) en vez de
    # desaparecer del Ingress.
    @app.get("/health")
    def salud():
        return {"estado": "ok", "servicio": config.servicio, "version": config.version}

    @app.get("/health/vivo")
    def vivo():
        return {"estado": "ok"}

    @app.get("/metrics")
    def exportar_metricas():
        return Response(generate_latest(registro), media_type=CONTENT_TYPE_LATEST)

    FastAPIInstrumentor.instrument_app(app, excluded_urls="health,metrics")
    return app
