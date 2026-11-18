import pytest

from tests.conftest import CANCELADA, VENCIDA, RepositorioFalso, crear_cliente, pago


# ── Pagar ────────────────────────────────────────────────────────────
def test_paga_una_reservacion(cliente):
    r = cliente.post("/ordenes", json=pago())
    assert r.status_code == 201
    orden = r.json()
    assert orden["estado"] == "pagada"
    assert orden["total"] == "4800.00"
    assert orden["artista"] == "Humbe"
    assert orden["cliente_email"] == "ana@ejemplo.com"          # se guarda en minúsculas
    assert len(orden["asientos"]) == 2


def test_no_se_cobra_dos_veces(cliente):
    primera = cliente.post("/ordenes", json=pago()).json()
    r = cliente.post("/ordenes", json=pago())
    assert r.status_code == 409
    assert r.json()["motivo"] == "ya_pagada"
    assert r.json()["orden_id"] == primera["id"]                # le dice cuál es su orden


@pytest.mark.parametrize("reservacion, motivo", [(VENCIDA, "vencida"), (CANCELADA, "cancelada")])
def test_reservaciones_que_no_se_pueden_pagar(cliente, reservacion, motivo):
    r = cliente.post("/ordenes", json=pago(reservacion_id=str(reservacion)))
    assert r.status_code == 409
    assert r.json()["motivo"] == motivo


def test_reservacion_inexistente_da_404(cliente):
    r = cliente.post("/ordenes", json=pago(reservacion_id="44444444-4444-4444-4444-444444444444"))
    assert r.status_code == 404


@pytest.mark.parametrize("cambio", [
    {"cliente_email": "sin-arroba"}, {"cliente_nombre": "A"}, {"reservacion_id": "no-es-uuid"},
])
def test_datos_invalidos_dan_422(cliente, cambio):
    assert cliente.post("/ordenes", json=pago(**cambio)).status_code == 422


# ── Consultar ────────────────────────────────────────────────────────
def test_consulta_orden(cliente):
    orden = cliente.post("/ordenes", json=pago()).json()
    assert cliente.get(f"/ordenes/{orden['id']}").json()["id"] == orden["id"]
    assert cliente.get("/ordenes/999999").status_code == 404


# ── Salud y métricas ─────────────────────────────────────────────────
def test_health(cliente):
    assert cliente.get("/health").status_code == 200
    with crear_cliente(repo=RepositorioFalso(base_ok=False)) as c:
        assert c.get("/health").status_code == 503
        assert c.get("/health/vivo").status_code == 200


def test_metricas_de_ventas(cliente):
    cliente.post("/ordenes", json=pago())
    cliente.post("/ordenes", json=pago())                       # rechazada: ya pagada
    texto = cliente.get("/metrics").text
    assert 'boletera_ordenes_total{evento_id="1"} 1.0' in texto
    assert 'boletera_ventas_boletos_total{evento_id="1",seccion="VIP"} 2.0' in texto
    assert 'boletera_ingresos_pesos_total{evento_id="1"} 4800.0' in texto
    assert 'boletera_pagos_rechazados_total{motivo="ya_pagada"} 1.0' in texto
    assert 'http_requests_total{method="POST",route="/ordenes",status="201"} 1.0' in texto
    assert 'boletera_info{servicio="svc-ordenes",version="prueba"} 1.0' in texto


def test_error_rate():
    with crear_cliente(error_rate=1.0) as c:
        assert c.post("/ordenes", json=pago()).status_code == 500
        assert c.get("/health").status_code == 200
