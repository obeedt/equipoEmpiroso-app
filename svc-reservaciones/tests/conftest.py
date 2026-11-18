"""Pruebas sin base de datos: un repositorio falso, en memoria, reemplaza a PostgreSQL."""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.main import crear_app
from app.repositorio import Conflicto, NoEncontrado, SinLugares

PRECIOS = {"VIP": Decimal("2400.00"), "Preferente": Decimal("1800.00"), "General": Decimal("1200.00")}


class RepositorioFalso:
    """Evento 1 con 5 lugares libres por sección. Evento 2 no existe."""

    def __init__(self, base_ok: bool = True):
        self.base_ok = base_ok
        self.libres = {s: 5 for s in PRECIOS}
        self.reservaciones = {}
        self.siguiente_asiento = 1

    def abrir(self):
        pass

    def cerrar(self):
        pass

    def salud(self):
        if not self.base_ok:
            raise ConnectionError("sin conexión")
        return True

    def crear(self, evento_id, seccion, cantidad, sesion, minutos):
        if evento_id != 1:
            raise NoEncontrado("Evento no encontrado")
        if self.libres[seccion] < cantidad:
            raise SinLugares(self.libres[seccion])
        self.libres[seccion] -= cantidad
        asientos = []
        for _ in range(cantidad):
            asientos.append({"id": self.siguiente_asiento, "seccion": seccion, "fila": "1",
                             "numero": self.siguiente_asiento, "precio": PRECIOS[seccion]})
            self.siguiente_asiento += 1
        ahora = datetime.now(timezone.utc)
        r = {"id": uuid.uuid4(), "evento_id": evento_id, "sesion": sesion, "estado": "activa",
             "seccion": seccion, "cantidad": cantidad, "total": PRECIOS[seccion] * cantidad,
             "expira_en": ahora + timedelta(minutes=minutos), "creada_en": ahora, "asientos": asientos}
        self.reservaciones[r["id"]] = r
        return r

    def obtener(self, reservacion_id):
        if reservacion_id not in self.reservaciones:
            raise NoEncontrado("Reservación no encontrada")
        return self.reservaciones[reservacion_id]

    def cancelar(self, reservacion_id):
        r = self.obtener(reservacion_id)
        if r["estado"] == "confirmada":
            raise Conflicto("La reservación ya se pagó")
        if r["estado"] != "activa":
            return 0
        r["estado"] = "cancelada"
        self.libres[r["seccion"]] += r["cantidad"]
        return r["cantidad"]

    def liberar_vencidas(self):
        return 0, 0


@pytest.fixture
def repo():
    return RepositorioFalso()


@pytest.fixture
def cliente(repo):
    app = crear_app(Config(version="prueba", liberador_segundos=0), repositorio=repo)
    with TestClient(app) as c:
        yield c


def crear_cliente(**config):
    repo = config.pop("repo", RepositorioFalso())
    return TestClient(crear_app(Config(version="prueba", liberador_segundos=0, **config), repositorio=repo))


def pedido(**cambios):
    datos = {"evento_id": 1, "seccion": "Preferente", "cantidad": 2, "sesion": "sesion-de-prueba-1"}
    datos.update(cambios)
    return datos
