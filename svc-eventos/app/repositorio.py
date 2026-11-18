"""Acceso a PostgreSQL. Solo lectura: svc-eventos no escribe en la base.

Usa las vistas del esquema (tienda/postgres/init/01-esquema.sql en el repo
GitOps), para que la disponibilidad coincida con la de los demás servicios:
  - asientos_estado_actual: un bloqueo vencido cuenta como disponible
  - ventas_por_evento: la métrica de negocio (boletos vendidos por evento)
"""
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor

SQL_EVENTOS = """
SELECT e.id, e.nombre, e.artista, e.genero, e.descripcion, e.recinto, e.ciudad,
       e.fecha, e.precio_base, e.capacidad, v.disponibles, v.porcentaje_vendido
FROM eventos e
JOIN ventas_por_evento v ON v.id = e.id
WHERE e.activo
  AND (%(ciudad)s::text IS NULL OR e.ciudad = %(ciudad)s)
  AND (%(genero)s::text IS NULL OR e.genero = %(genero)s)
ORDER BY e.fecha
"""

SQL_EVENTO = """
SELECT e.id, e.nombre, e.artista, e.genero, e.descripcion, e.recinto, e.ciudad,
       e.fecha, e.precio_base, e.capacidad, v.disponibles, v.porcentaje_vendido
FROM eventos e
JOIN ventas_por_evento v ON v.id = e.id
WHERE e.activo AND e.id = %(id)s
"""

SQL_SECCIONES = """
SELECT seccion,
       precio,
       count(*)                                     AS total,
       count(*) FILTER (WHERE estado = 'disponible') AS disponibles
FROM asientos_estado_actual
WHERE evento_id = %(id)s
GROUP BY seccion, precio
ORDER BY precio DESC
"""

SQL_VENTAS = """
SELECT id, nombre, artista, recinto, capacidad, vendidos, bloqueados, disponibles, porcentaje_vendido
FROM ventas_por_evento
"""


class Repositorio:
    def __init__(self, conninfo: str, max_conexiones: int):
        # Cada conexión nueva del pool se instrumenta: cada consulta aparece
        # como un tramo (span) "postgresql" dentro de la traza de la petición.
        self.pool = ConnectionPool(
            conninfo,
            min_size=1,
            max_size=max_conexiones,
            open=False,
            timeout=5,
            kwargs={"row_factory": dict_row, "autocommit": True},
            configure=PsycopgInstrumentor.instrument_connection,
            name="boletera",
        )

    def abrir(self) -> None:
        # wait=False: el servicio arranca aunque la base todavía no responda;
        # /health avisa a Kubernetes cuando ya está lista.
        self.pool.open(wait=False)

    def cerrar(self) -> None:
        self.pool.close()

    def _consulta(self, sql: str, params: dict | None = None) -> list[dict]:
        with self.pool.connection() as conexion:
            return conexion.execute(sql, params or {}).fetchall()

    def salud(self) -> bool:
        with self.pool.connection(timeout=2) as conexion:
            conexion.execute("SELECT 1")
        return True

    def listar_eventos(self, ciudad: str | None, genero: str | None) -> list[dict]:
        return self._consulta(SQL_EVENTOS, {"ciudad": ciudad, "genero": genero})

    def obtener_evento(self, evento_id: int) -> dict | None:
        filas = self._consulta(SQL_EVENTO, {"id": evento_id})
        if not filas:
            return None
        evento = filas[0]
        evento["secciones"] = self._consulta(SQL_SECCIONES, {"id": evento_id})
        return evento

    def ventas_por_evento(self) -> list[dict]:
        return self._consulta(SQL_VENTAS)
