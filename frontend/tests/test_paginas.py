import httpx
from fastapi.testclient import TestClient

from app.config import Config
from app.main import crear_app, dinero, fecha
from tests.conftest import ServiciosFalsos


def comprar(cliente, cantidad=2):
    return cliente.post("/carrito", data={"evento_id": 1, "seccion": "Preferente", "cantidad": cantidad})


# ── Formato ──────────────────────────────────────────────────────────
def test_formatos():
    assert dinero("1800.00") == "$1,800.00"
    assert fecha("2027-01-23T21:00:00-06:00") == "sáb 23 ene 2027 · 21:00"


# ── Catálogo y evento ────────────────────────────────────────────────
def test_catalogo(cliente):
    r = cliente.get("/")
    assert r.status_code == 200
    assert "Humbe" in r.text and "Enjambre" in r.text
    assert "¡Últimos boletos!" in r.text                       # Humbe tiene 92 % vendido
    assert "Desde $1,200.00" in r.text


def test_filtro_por_ciudad(cliente):
    r = cliente.get("/", params={"ciudad": "Monterrey"})
    assert "Enjambre" in r.text and "Humbe" not in r.text


def test_pagina_de_evento(cliente):
    r = cliente.get("/evento/1")
    assert r.status_code == 200
    assert "Preferente" in r.text and "$1,800.00" in r.text
    assert '<option value="General"' not in r.text            # General está agotado: no se ofrece


def test_evento_inexistente(cliente):
    assert cliente.get("/evento/999").status_code == 404


# ── Flujo de compra: catálogo → carrito → pedido ─────────────────────
def test_compra_completa(cliente, servicios):
    r = comprar(cliente)
    assert r.status_code == 303 and r.headers["location"] == "/carrito"
    assert cliente.cookies.get("reservacion")
    assert cliente.cookies.get("sesion")

    carrito = cliente.get("/carrito")
    assert "Fila" in carrito.text and "$3,600.00" in carrito.text

    r = cliente.post("/pagar", data={"cliente_nombre": "Ana Prueba", "cliente_email": "ana@ejemplo.com"})
    assert r.status_code == 303 and r.headers["location"] == "/orden/500"
    assert not cliente.cookies.get("reservacion")              # el carrito se vacía

    orden = cliente.get("/orden/500")
    assert "Pedido #500" in orden.text and "¡Listo, Ana Prueba!" in orden.text


def test_sin_lugares_muestra_cuantos_quedan(cliente):
    r = comprar(cliente, cantidad=8)
    assert r.status_code == 409
    assert "quedan 5" in r.text


def test_cancelar_vacia_el_carrito(cliente, servicios):
    comprar(cliente)
    r = cliente.post("/carrito/cancelar")
    assert r.status_code == 303
    assert all(x["estado"] == "cancelada" for x in servicios.reservaciones.values())
    assert "Tu carrito está vacío" in cliente.get("/carrito").text


def test_carrito_vencido(cliente, servicios):
    comprar(cliente)
    servicios.estado_pago = "vencida"
    r = cliente.post("/pagar", data={"cliente_nombre": "Ana", "cliente_email": "ana@ejemplo.com"})
    assert r.status_code == 409
    assert "Se acabó el tiempo" in r.text


# ── Un servicio caído: página de error con folio ─────────────────────
def test_servicio_caido_da_503_con_folio(cliente, servicios):
    servicios.caido = "svc-eventos"
    r = cliente.get("/")
    assert r.status_code == 503
    assert "Folio de error" in r.text
    texto = cliente.get("/metrics").text
    assert 'boletera_llamadas_backend_total{resultado="sin_respuesta",servicio="svc-eventos"} 1.0' in texto
    assert 'http_requests_total{method="GET",route="/",status="503"} 1.0' in texto


def test_health_no_depende_de_los_otros_servicios(cliente, servicios):
    servicios.caido = "svc-eventos"
    assert cliente.get("/health").status_code == 200


def test_error_rate():
    app = crear_app(Config(version="prueba", error_rate=1.0), transporte=httpx.MockTransport(ServiciosFalsos()))
    with TestClient(app) as c:
        assert c.get("/").status_code == 503
        assert c.get("/health").status_code == 200


def test_banner_y_version():
    app = crear_app(Config(version="abc1234", banner="¡Preventa Rels B!"),
                    transporte=httpx.MockTransport(ServiciosFalsos()))
    with TestClient(app) as c:
        texto = c.get("/").text
        assert "¡Preventa Rels B!" in texto and "versión abc1234" in texto
