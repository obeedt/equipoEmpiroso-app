"""Pruebas sin base de datos: un repositorio falso, en memoria, reemplaza a PostgreSQL."""
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.main import crear_app
from app.repositorio import NoEncontrado, Rechazado

ACTIVA = uuid.UUID("11111111-1111-1111-1111-111111111111")
VENCIDA = uuid.UUID("22222222-2222-2222-2222-222222222222")
CANCELADA = uuid.UUID("33333333-3333-3333-3333-333333333333")


class RepositorioFalso:
    def __init__(self, base_ok: bool = True):
        self.base_ok = base_ok
        self.reservaciones = {ACTIVA: "activa", VENCIDA: "vencida", CANCELADA: "cancelada"}
        self.ordenes = {}

    def abrir(self):
        pass

    def cerrar(self):
        pass

    def salud(self):
        if not self.base_ok:
            raise ConnectionError("sin conexión")
        return True

    def pagar(self, reservacion_id, nombre, email):
        estado = self.reservaciones.get(reservacion_id)
        if estado is None:
            raise NoEncontrado("Reservación no encontrada")
        if estado == "confirmada":
            orden_id = next(o["id"] for o in self.ordenes.values() if o["reservacion_id"] == reservacion_id)
            raise Rechazado("ya_pagada", orden_id)
        if estado != "activa":
            raise Rechazado(estado)
        self.reservaciones[reservacion_id] = "confirmada"
        orden_id = len(self.ordenes) + 100
        asientos = [
            {"id": 1, "seccion": "VIP", "fila": "1", "numero": 1, "precio": Decimal("2400.00")},
            {"id": 2, "seccion": "VIP", "fila": "1", "numero": 2, "precio": Decimal("2400.00")},
        ]
        self.ordenes[orden_id] = {
            "id": orden_id, "evento_id": 1, "evento": "Dueño del Cielo Tour Finale", "artista": "Humbe",
            "recinto": "Arena CDMX", "ciudad": "Ciudad de México",
            "fecha": datetime(2026, 12, 5, 3, 0, tzinfo=timezone.utc), "reservacion_id": reservacion_id,
            "cliente_nombre": nombre, "cliente_email": email, "cantidad": 2, "total": Decimal("4800.00"),
            "estado": "pagada", "creada_en": datetime.now(timezone.utc), "asientos": asientos,
        }
        return self.ordenes[orden_id]

    def obtener(self, orden_id):
        if orden_id not in self.ordenes:
            raise NoEncontrado("Orden no encontrada")
        return self.ordenes[orden_id]


@pytest.fixture
def repo():
    return RepositorioFalso()


@pytest.fixture
def cliente(repo):
    with TestClient(crear_app(Config(version="prueba"), repositorio=repo)) as c:
        yield c


def crear_cliente(**config):
    repo = config.pop("repo", RepositorioFalso())
    return TestClient(crear_app(Config(version="prueba", **config), repositorio=repo))


def pago(**cambios):
    datos = {"reservacion_id": str(ACTIVA), "cliente_nombre": "Ana Prueba", "cliente_email": "Ana@Ejemplo.com"}
    datos.update(cambios)
    return datos
