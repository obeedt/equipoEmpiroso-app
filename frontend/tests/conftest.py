"""Pruebas sin los otros servicios: httpx.MockTransport responde por ellos."""
import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.main import crear_app

EVENTO = {
    "id": 1, "nombre": "Dueño del Cielo Tour Finale", "artista": "Humbe", "genero": "Pop",
    "descripcion": "Cierre de la gira.", "recinto": "Arena CDMX", "ciudad": "Ciudad de México",
    "fecha": "2026-12-04T21:00:00-06:00", "precio_base": "1200.00", "capacidad": 22300,
    "disponibles": 1788, "porcentaje_vendido": "92.0",
}
EVENTO_MTY = {**EVENTO, "id": 2, "artista": "Enjambre", "nombre": "Enjambre en concierto",
              "ciudad": "Monterrey", "genero": "Rock alternativo", "porcentaje_vendido": "80.0"}
SECCIONES = [
    {"seccion": "VIP", "precio": "2400.00", "total": 1115, "disponibles": 91},
    {"seccion": "Preferente", "precio": "1800.00", "total": 5575, "disponibles": 447},
    {"seccion": "General", "precio": "1200.00", "total": 15610, "disponibles": 0},
]


class ServiciosFalsos:
    """Imita a svc-eventos, svc-reservaciones y svc-ordenes."""

    def __init__(self):
        self.caido = None             # nombre del host que "no responde"
        self.reservaciones = {}
        self.pedidos = []
        self.estado_pago = None       # para forzar 409 de svc-ordenes: "vencida", etc.

    def __call__(self, request: httpx.Request) -> httpx.Response:
        host, metodo, ruta = request.url.host, request.method, request.url.path
        if host == self.caido:
            raise httpx.ConnectError("conexión rechazada", request=request)
        if host == "svc-eventos":
            if ruta == "/eventos":
                return httpx.Response(200, json=[EVENTO, EVENTO_MTY])
            if ruta == "/eventos/1":
                return httpx.Response(200, json={**EVENTO, "secciones": SECCIONES})
            return httpx.Response(404, json={"detail": "Evento no encontrado"})
        if host == "svc-reservaciones":
            if metodo == "POST":
                datos = json.loads(request.content)
                if datos["cantidad"] > 5:
                    return httpx.Response(409, json={"detail": "sin lugares", "disponibles": 5})
                r = {"id": str(uuid.uuid4()), "evento_id": datos["evento_id"], "sesion": datos["sesion"],
                     "estado": "activa", "seccion": datos["seccion"], "cantidad": datos["cantidad"],
                     "total": "3600.00", "expira_en": "2026-11-18T13:22:00-06:00",
                     "creada_en": "2026-11-18T13:12:00-06:00",
                     "asientos": [{"id": i, "seccion": datos["seccion"], "fila": "7", "numero": i,
                                   "precio": "1800.00"} for i in range(1, datos["cantidad"] + 1)]}
                self.reservaciones[r["id"]] = r
                return httpx.Response(201, json=r)
            rid = ruta.rsplit("/", 1)[-1]
            if rid not in self.reservaciones:
                return httpx.Response(404, json={"detail": "no"})
            if metodo == "DELETE":
                self.reservaciones[rid]["estado"] = "cancelada"
                return httpx.Response(200, json={"reservacion_id": rid, "asientos_liberados": 2})
            return httpx.Response(200, json=self.reservaciones[rid])
        if host == "svc-ordenes":
            if metodo == "POST":
                datos = json.loads(request.content)
                if self.estado_pago:
                    return httpx.Response(409, json={"detail": "no", "motivo": self.estado_pago})
                r = self.reservaciones[datos["reservacion_id"]]
                orden = {"id": 500 + len(self.pedidos), "evento_id": 1, "evento": EVENTO["nombre"],
                         "artista": "Humbe", "recinto": "Arena CDMX", "ciudad": "Ciudad de México",
                         "fecha": EVENTO["fecha"], "reservacion_id": r["id"],
                         "cliente_nombre": datos["cliente_nombre"], "cliente_email": datos["cliente_email"],
                         "cantidad": r["cantidad"], "total": r["total"], "estado": "pagada",
                         "creada_en": "2026-11-18T13:13:00-06:00", "asientos": r["asientos"]}
                self.pedidos.append(orden)
                return httpx.Response(201, json=orden)
            for o in self.pedidos:
                if ruta == f"/ordenes/{o['id']}":
                    return httpx.Response(200, json=o)
            return httpx.Response(404, json={"detail": "no"})
        return httpx.Response(500)


@pytest.fixture
def servicios():
    return ServiciosFalsos()


@pytest.fixture
def cliente(servicios):
    app = crear_app(Config(version="prueba"), transporte=httpx.MockTransport(servicios))
    with TestClient(app, follow_redirects=False) as c:
        yield c
