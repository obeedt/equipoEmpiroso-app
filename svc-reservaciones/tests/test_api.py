import pytest

from tests.conftest import RepositorioFalso, crear_cliente, pedido


# ── Reservar ─────────────────────────────────────────────────────────
def test_reserva_lugares(cliente):
    r = cliente.post("/reservaciones", json=pedido())
    assert r.status_code == 201
    datos = r.json()
    assert datos["estado"] == "activa"
    assert datos["cantidad"] == 2
    assert len(datos["asientos"]) == 2
    assert datos["total"] == "3600.00"                 # 2 x 1800
    assert datos["seccion"] == "Preferente"


def test_sin_lugares_da_409_y_dice_cuantos_quedan(cliente):
    assert cliente.post("/reservaciones", json=pedido(cantidad=4)).status_code == 201
    r = cliente.post("/reservaciones", json=pedido(cantidad=2))     # solo queda 1
    assert r.status_code == 409
    assert r.json()["disponibles"] == 1


def test_evento_inexistente_da_404(cliente):
    assert cliente.post("/reservaciones", json=pedido(evento_id=2)).status_code == 404


@pytest.mark.parametrize("cambio", [
    {"cantidad": 0}, {"cantidad": 11}, {"seccion": "Palco"}, {"sesion": "corta"}, {"evento_id": 0},
])
def test_datos_invalidos_dan_422(cliente, cambio):
    assert cliente.post("/reservaciones", json=pedido(**cambio)).status_code == 422


# ── Consultar y cancelar ─────────────────────────────────────────────
def test_consulta_reservacion(cliente):
    creada = cliente.post("/reservaciones", json=pedido()).json()
    r = cliente.get(f"/reservaciones/{creada['id']}")
    assert r.status_code == 200
    assert r.json()["id"] == creada["id"]


def test_id_invalido_da_422_e_inexistente_404(cliente):
    assert cliente.get("/reservaciones/no-es-uuid").status_code == 422
    assert cliente.get("/reservaciones/00000000-0000-0000-0000-000000000000").status_code == 404


def test_cancelar_libera_los_lugares(cliente, repo):
    creada = cliente.post("/reservaciones", json=pedido(cantidad=5)).json()
    assert repo.libres["Preferente"] == 0
    r = cliente.delete(f"/reservaciones/{creada['id']}")
    assert r.status_code == 200
    assert r.json()["asientos_liberados"] == 5
    assert repo.libres["Preferente"] == 5
    assert cliente.delete(f"/reservaciones/{creada['id']}").json()["asientos_liberados"] == 0   # idempotente


def test_no_se_cancela_una_reservacion_pagada(cliente, repo):
    creada = cliente.post("/reservaciones", json=pedido()).json()
    repo.reservaciones[next(iter(repo.reservaciones))]["estado"] = "confirmada"
    assert cliente.delete(f"/reservaciones/{creada['id']}").status_code == 409


# ── Salud y métricas ─────────────────────────────────────────────────
def test_health(cliente):
    assert cliente.get("/health").status_code == 200
    with crear_cliente(repo=RepositorioFalso(base_ok=False)) as c:
        assert c.get("/health").status_code == 503
        assert c.get("/health/vivo").status_code == 200


def test_metricas(cliente):
    cliente.post("/reservaciones", json=pedido(cantidad=3))
    cliente.post("/reservaciones", json=pedido(cantidad=3))          # sin lugares
    texto = cliente.get("/metrics").text
    assert 'boletera_reservaciones_total{resultado="creada"} 1.0' in texto
    assert 'boletera_reservaciones_total{resultado="sin_lugares"} 1.0' in texto
    assert 'boletera_boletos_bloqueados_total{evento_id="1",seccion="Preferente"} 3.0' in texto
    assert 'http_requests_total{method="POST",route="/reservaciones",status="409"} 1.0' in texto
    assert 'boletera_info{servicio="svc-reservaciones",version="prueba"} 1.0' in texto


def test_error_rate(cliente):
    with crear_cliente(error_rate=1.0) as c:
        assert c.post("/reservaciones", json=pedido()).status_code == 500
        assert c.get("/health").status_code == 200
