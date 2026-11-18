"""Pruebas sin base de datos: un repositorio falso reemplaza a PostgreSQL."""
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.main import crear_app

EVENTOS = [
    {
        "id": 1, "nombre": "Dueño del Cielo Tour Finale", "artista": "Humbe", "genero": "Pop",
        "descripcion": "Cierre de la gira.", "recinto": "Arena CDMX", "ciudad": "Ciudad de México",
        "fecha": datetime(2026, 12, 5, 3, 0, tzinfo=timezone.utc), "precio_base": Decimal("1200.00"),
        "capacidad": 22300, "disponibles": 1788, "porcentaje_vendido": Decimal("92.0"),
    },
    {
        "id": 2, "nombre": "Enjambre en concierto", "artista": "Enjambre", "genero": "Rock alternativo",
        "descripcion": "Rock alternativo.", "recinto": "Auditorio Banamex", "ciudad": "Monterrey",
        "fecha": datetime(2026, 12, 18, 3, 0, tzinfo=timezone.utc), "precio_base": Decimal("950.00"),
        "capacidad": 7000, "disponibles": 1400, "porcentaje_vendido": Decimal("80.0"),
    },
]

SECCIONES = [
    {"seccion": "VIP", "precio": Decimal("2400.00"), "total": 1115, "disponibles": 91},
    {"seccion": "Preferente", "precio": Decimal("1800.00"), "total": 5575, "disponibles": 447},
    {"seccion": "General", "precio": Decimal("1200.00"), "total": 15610, "disponibles": 1250},
]


class RepositorioFalso:
    def __init__(self, base_ok: bool = True):
        self.base_ok = base_ok
        self.filtros = None

    def abrir(self):
        pass

    def cerrar(self):
        pass

    def salud(self):
        if not self.base_ok:
            raise ConnectionError("sin conexión")
        return True

    def listar_eventos(self, ciudad, genero):
        self.filtros = (ciudad, genero)
        return [e for e in EVENTOS if ciudad in (None, e["ciudad"]) and genero in (None, e["genero"])]

    def obtener_evento(self, evento_id):
        for e in EVENTOS:
            if e["id"] == evento_id:
                return {**e, "secciones": SECCIONES}
        return None

    def ventas_por_evento(self):
        if not self.base_ok:
            raise ConnectionError("sin conexión")
        return [
            {"id": e["id"], "nombre": e["nombre"], "artista": e["artista"], "recinto": e["recinto"],
             "capacidad": e["capacidad"], "vendidos": e["capacidad"] - e["disponibles"], "bloqueados": 0,
             "disponibles": e["disponibles"], "porcentaje_vendido": e["porcentaje_vendido"]}
            for e in EVENTOS
        ]


@pytest.fixture
def repo():
    return RepositorioFalso()


@pytest.fixture
def cliente(repo):
    app = crear_app(Config(version="prueba"), repositorio=repo)
    with TestClient(app) as c:
        yield c


def crear_cliente(**config):
    """Cliente con configuración especial (p. ej. error_rate=1.0)."""
    repo = config.pop("repo", RepositorioFalso())
    return TestClient(crear_app(Config(version="prueba", **config), repositorio=repo))
