"""Acceso a PostgreSQL de svc-reservaciones.

Sigue el "contrato de datos" del repo GitOps (tienda/README.md):
  - Bloquear: los mejores lugares libres de una sección, en una sola
    sentencia con FOR UPDATE SKIP LOCKED. Dos compras simultáneas reciben
    lugares distintos y nunca el mismo asiento.
  - Un bloqueo vencido (bloqueado_hasta <= now()) cuenta como libre.
  - svc-reservaciones solo escribe en "reservaciones" y "asientos".
"""
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor


class NoEncontrado(Exception):
    """El evento o la reservación no existen."""


class SinLugares(Exception):
    """No quedan suficientes lugares en la sección."""

    def __init__(self, disponibles: int):
        super().__init__(f"Solo hay {disponibles} lugares disponibles")
        self.disponibles = disponibles


class Conflicto(Exception):
    """La operación no se puede hacer en el estado actual (p. ej. ya se pagó)."""


SQL_EVENTO_ACTIVO = "SELECT id FROM eventos WHERE id = %(evento_id)s AND activo"

SQL_NUEVA_RESERVACION = """
INSERT INTO reservaciones (evento_id, sesion, expira_en)
VALUES (%(evento_id)s, %(sesion)s, now() + make_interval(mins => %(minutos)s))
RETURNING id, evento_id, sesion, estado, expira_en, creada_en
"""

# Los ids van de los mejores lugares a los peores (02-datos.sql)
SQL_BLOQUEAR = """
WITH elegidos AS (
    SELECT id FROM asientos
    WHERE evento_id = %(evento_id)s AND seccion = %(seccion)s
      AND (estado = 'disponible' OR (estado = 'bloqueado' AND bloqueado_hasta <= now()))
    ORDER BY id
    LIMIT %(cantidad)s
    FOR UPDATE SKIP LOCKED
)
UPDATE asientos a
SET estado = 'bloqueado', reservacion_id = %(reservacion_id)s, bloqueado_hasta = %(expira_en)s
FROM elegidos e
WHERE a.id = e.id
RETURNING a.id, a.seccion, a.fila, a.numero, a.precio
"""

SQL_RESERVACION = """
SELECT id, evento_id, sesion, expira_en, creada_en,
       CASE WHEN estado = 'activa' AND expira_en <= now() THEN 'expirada' ELSE estado END AS estado
FROM reservaciones
WHERE id = %(id)s
"""

SQL_ASIENTOS_DE_RESERVACION = """
SELECT id, seccion, fila, numero, precio
FROM asientos
WHERE reservacion_id = %(id)s
ORDER BY id
"""

SQL_BLOQUEAR_RESERVACION = "SELECT estado FROM reservaciones WHERE id = %(id)s FOR UPDATE"

SQL_LIBERAR_ASIENTOS = """
UPDATE asientos
SET estado = 'disponible', reservacion_id = NULL, bloqueado_hasta = NULL
WHERE reservacion_id = ANY(%(ids)s) AND estado = 'bloqueado'
"""

SQL_VENCER = """
UPDATE reservaciones SET estado = 'expirada'
WHERE estado = 'activa' AND expira_en <= now()
RETURNING id
"""


def _resumen(reservacion: dict, asientos: list[dict]) -> dict:
    return {
        **reservacion,
        "seccion": asientos[0]["seccion"] if asientos else None,
        "cantidad": len(asientos),
        "total": sum((a["precio"] for a in asientos), 0),
        "asientos": asientos,
    }


class Repositorio:
    def __init__(self, conninfo: str, max_conexiones: int):
        # Cada consulta aparece como un tramo "postgresql" en la traza
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
        self.pool.open(wait=False)

    def cerrar(self) -> None:
        self.pool.close()

    def salud(self) -> bool:
        with self.pool.connection(timeout=2) as conexion:
            conexion.execute("SELECT 1")
        return True

    def crear(self, evento_id: int, seccion: str, cantidad: int, sesion: str, minutos: int) -> dict:
        """Crea la reservación y bloquea los lugares, todo o nada."""
        with self.pool.connection() as conexion, conexion.transaction():
            if conexion.execute(SQL_EVENTO_ACTIVO, {"evento_id": evento_id}).fetchone() is None:
                raise NoEncontrado("Evento no encontrado")
            reservacion = conexion.execute(SQL_NUEVA_RESERVACION, {
                "evento_id": evento_id, "sesion": sesion, "minutos": minutos,
            }).fetchone()
            asientos = conexion.execute(SQL_BLOQUEAR, {
                "evento_id": evento_id, "seccion": seccion, "cantidad": cantidad,
                "reservacion_id": reservacion["id"], "expira_en": reservacion["expira_en"],
            }).fetchall()
            if len(asientos) < cantidad:
                # La excepción deshace la transacción: no queda nada bloqueado
                raise SinLugares(len(asientos))
        return _resumen(reservacion, sorted(asientos, key=lambda a: a["id"]))

    def obtener(self, reservacion_id) -> dict:
        with self.pool.connection() as conexion:
            reservacion = conexion.execute(SQL_RESERVACION, {"id": reservacion_id}).fetchone()
            if reservacion is None:
                raise NoEncontrado("Reservación no encontrada")
            asientos = conexion.execute(SQL_ASIENTOS_DE_RESERVACION, {"id": reservacion_id}).fetchall()
        return _resumen(reservacion, asientos)

    def cancelar(self, reservacion_id) -> int:
        """Libera los asientos de una reservación activa. Regresa cuántos liberó."""
        with self.pool.connection() as conexion, conexion.transaction():
            fila = conexion.execute(SQL_BLOQUEAR_RESERVACION, {"id": reservacion_id}).fetchone()
            if fila is None:
                raise NoEncontrado("Reservación no encontrada")
            if fila["estado"] == "confirmada":
                raise Conflicto("La reservación ya se pagó")
            if fila["estado"] != "activa":
                return 0                    # ya cancelada o vencida: nada que hacer
            liberados = conexion.execute(SQL_LIBERAR_ASIENTOS, {"ids": [reservacion_id]}).rowcount
            conexion.execute("UPDATE reservaciones SET estado = 'cancelada' WHERE id = %(id)s",
                             {"id": reservacion_id})
        return liberados

    def liberar_vencidas(self) -> tuple[int, int]:
        """Marca como vencidas las reservaciones sin pagar y libera sus asientos.

        La disponibilidad ya trata un bloqueo vencido como libre (vista
        asientos_estado_actual); esto solo deja la base ordenada.
        Regresa (reservaciones vencidas, asientos liberados)."""
        with self.pool.connection() as conexion, conexion.transaction():
            ids = [f["id"] for f in conexion.execute(SQL_VENCER).fetchall()]
            if not ids:
                return 0, 0
            liberados = conexion.execute(SQL_LIBERAR_ASIENTOS, {"ids": ids}).rowcount
        return len(ids), liberados
