import json
import logging

from opentelemetry import trace

from app.observabilidad import FormatoJSON
from tests.conftest import RepositorioFalso, crear_cliente


# ── Catálogo ─────────────────────────────────────────────────────────
def test_lista_eventos(cliente):
    r = cliente.get("/eventos")
    assert r.status_code == 200
    datos = r.json()
    assert [e["artista"] for e in datos] == ["Humbe", "Enjambre"]
    assert datos[0]["precio_base"] == "1200.00"          # el dinero viaja como texto exacto


def test_filtra_por_ciudad(cliente, repo):
    r = cliente.get("/eventos", params={"ciudad": "Monterrey"})
    assert r.status_code == 200
    assert [e["id"] for e in r.json()] == [2]
    assert repo.filtros == ("Monterrey", None)


def test_detalle_con_secciones(cliente):
    r = cliente.get("/eventos/1")
    assert r.status_code == 200
    secciones = r.json()["secciones"]
    assert [s["seccion"] for s in secciones] == ["VIP", "Preferente", "General"]
    assert secciones[2]["disponibles"] == 1250


def test_evento_inexistente_da_404(cliente):
    assert cliente.get("/eventos/999").status_code == 404


# ── Salud ────────────────────────────────────────────────────────────
def test_health_ok(cliente):
    r = cliente.get("/health")
    assert r.status_code == 200
    assert r.json()["base_de_datos"] == "ok"


def test_health_sin_base_da_503():
    with crear_cliente(repo=RepositorioFalso(base_ok=False)) as c:
        assert c.get("/health").status_code == 503
        assert c.get("/health/vivo").status_code == 200   # vivo no depende de la base


# ── Métricas ─────────────────────────────────────────────────────────
def test_metricas_http_y_de_negocio(cliente):
    cliente.get("/eventos")
    cliente.get("/eventos/1")
    cliente.get("/eventos/999")
    texto = cliente.get("/metrics").text
    assert 'http_requests_total{method="GET",route="/eventos",status="200"} 1.0' in texto
    assert 'route="/eventos/{evento_id}",status="404"' in texto     # plantilla, no la URL real
    assert "http_request_duration_seconds_bucket" in texto
    assert 'boletera_porcentaje_vendido{artista="Humbe",evento="Dueño del Cielo Tour Finale",evento_id="1",recinto="Arena CDMX"} 92.0' in texto
    assert "boletera_metricas_negocio_ok 1.0" in texto
    assert 'boletera_info{servicio="svc-eventos",version="prueba"} 1.0' in texto


def test_metricas_sin_base_no_rompen_metrics():
    with crear_cliente(repo=RepositorioFalso(base_ok=False)) as c:
        r = c.get("/metrics")
        assert r.status_code == 200
        assert "boletera_metricas_negocio_ok 0.0" in r.text


# ── Falla inyectada (demo 3) ─────────────────────────────────────────
def test_error_rate_1_hace_fallar_la_api_pero_no_health():
    with crear_cliente(error_rate=1.0) as c:
        assert c.get("/eventos").status_code == 500
        assert c.get("/health").status_code == 200
        texto = c.get("/metrics").text
        assert 'route="/eventos",status="500"' in texto


# ── Logs ─────────────────────────────────────────────────────────────
def test_log_json_lleva_trace_id_dentro_de_una_traza():
    formato = FormatoJSON("svc-eventos")
    registro = logging.LogRecord("prueba", logging.ERROR, __file__, 1, "algo falló", None, None)
    registro.datos = {"codigo": 500}
    with trace.get_tracer("prueba").start_as_current_span("peticion"):
        linea = json.loads(formato.format(registro))
    assert linea["nivel"] == "ERROR"
    assert linea["servicio"] == "svc-eventos"
    assert linea["mensaje"] == "algo falló"
    assert linea["codigo"] == 500
    assert len(linea["trace_id"]) == 32
