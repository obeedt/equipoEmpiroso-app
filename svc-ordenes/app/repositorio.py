"""Acceso a PostgreSQL de svc-ordenes.

Sigue el "contrato de datos" del repo GitOps (tienda/README.md). El pago es
una sola transacción:
  1. Bloquea la reservación (FOR UPDATE) y revisa que siga activa y sin vencer.
  2. Toma sus asientos bloqueados y crea la orden con su total.
  3. Pasa los asientos a "vendido" y la reservación a "confirmada".
Si cualquier paso falla, no se guarda nada. ordenes.reservacion_id es UNIQUE:
una reservación no se puede cobrar dos veces, aunque el navegador reintente.
"""
from psycopg import errors
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor


class NoEncontrado(Exception):
    """La reservación o la orden no existen."""


class Rechazado(Exception):
    """El pago no procede. motivo: ya_pagada | vencida | cancelada | sin_asientos"""

    MENSAJES = {
        "ya_pagada": "La reservación ya se pagó",
        "vencida": "La reservación venció: los lugares ya no están apartados",
        "cancelada": "La reservación fue cancelada",
        "sin_asientos": "La reservación no tiene asientos apartados",
    }

    def __init__(self, motivo: str, orden_id: int | None = None):
        super().__init__(self.MENSAJES.get(motivo, motivo))
        self.motivo = motivo
        self.orden_id = orden_id


SQL_RESERVACION = """
SELECT id, evento_id, estado, expira_en <= now() AS vencida
FROM reservaciones
WHERE id = %(id)s
FOR UPDATE
"""

SQL_ORDEN_DE_RESERVACION = "SELECT id FROM ordenes WHERE reservacion_id = %(id)s"

SQL_ASIENTOS_APARTADOS = """
SELECT id, seccion, fila, numero, precio
FROM asientos
WHERE reservacion_id = %(id)s AND estado = 'bloqueado'
ORDER BY id
FOR UPDATE
"""

SQL_NUEVA_ORDEN = """
INSERT INTO ordenes (evento_id, reservacion_id, cliente_nombre, cliente_email, cantidad, total)
VALUES (%(evento_id)s, %(reservacion_id)s, %(nombre)s, %(email)s, %(cantidad)s, %(total)s)
RETURNING id
"""

SQL_VENDER = """
UPDATE asientos
SET estado = 'vendido', orden_id = %(orden_id)s, bloqueado_hasta = NULL
WHERE reservacion_id = %(reservacion_id)s AND estado = 'bloqueado'
"""

SQL_CONFIRMAR = "UPDATE reservaciones SET estado = 'confirmada' WHERE id = %(id)s"

SQL_ORDEN = """
SELECT o.id, o.evento_id, o.reservacion_id, o.cliente_nombre, o.cliente_email,
       o.cantidad, o.total, o.estado, o.creada_en,
       e.nombre AS evento, e.artista, e.recinto, e.ciudad, e.fecha
FROM ordenes o
JOIN eventos e ON e.id = o.evento_id
WHERE o.id = %(id)s
"""

SQL_ASIENTOS_DE_ORDEN = """
SELECT id, seccion, fila, numero, precio FROM asientos WHERE orden_id = %(id)s ORDER BY id
"""


class Repositorio:
    def __init__(self, conninfo: str, max_conexiones: int):
        # Cada consulta aparece como un tramo "postgresql" en la traza:
        # así se ve el recorrido frontend → svc-ordenes → BD
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

    def pagar(self, reservacion_id, nombre: str, email: str) -> dict:
        """Convierte una reservación activa en una orden pagada. Regresa la orden."""
        try:
            with self.pool.connection() as conexion, conexion.transaction():
                reservacion = conexion.execute(SQL_RESERVACION, {"id": reservacion_id}).fetchone()
                if reservacion is None:
                    raise NoEncontrado("Reservación no encontrada")
                if reservacion["estado"] == "confirmada":
                    orden = conexion.execute(SQL_ORDEN_DE_RESERVACION, {"id": reservacion_id}).fetchone()
                    raise Rechazado("ya_pagada", orden["id"] if orden else None)
                if reservacion["estado"] == "cancelada":
                    raise Rechazado("cancelada")
                if reservacion["estado"] != "activa" or reservacion["vencida"]:
                    raise Rechazado("vencida")

                asientos = conexion.execute(SQL_ASIENTOS_APARTADOS, {"id": reservacion_id}).fetchall()
                if not asientos:
                    raise Rechazado("sin_asientos")

                orden_id = conexion.execute(SQL_NUEVA_ORDEN, {
                    "evento_id": reservacion["evento_id"], "reservacion_id": reservacion_id,
                    "nombre": nombre, "email": email,
                    "cantidad": len(asientos), "total": sum(a["precio"] for a in asientos),
                }).fetchone()["id"]
                conexion.execute(SQL_VENDER, {"orden_id": orden_id, "reservacion_id": reservacion_id})
                conexion.execute(SQL_CONFIRMAR, {"id": reservacion_id})
        except errors.UniqueViolation:
            # Dos pagos simultáneos de la misma reservación: el segundo choca con el UNIQUE
            raise Rechazado("ya_pagada") from None
        return self.obtener(orden_id)

    def obtener(self, orden_id: int) -> dict:
        with self.pool.connection() as conexion:
            orden = conexion.execute(SQL_ORDEN, {"id": orden_id}).fetchone()
            if orden is None:
                raise NoEncontrado("Orden no encontrada")
            orden["asientos"] = conexion.execute(SQL_ASIENTOS_DE_ORDEN, {"id": orden_id}).fetchall()
        return orden
