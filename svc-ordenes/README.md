# svc-ordenes

Pago de reservaciones y pedidos de la boletera. Convierte una reservación
activa en una **orden pagada** y marca sus asientos como vendidos.

Imagen: `docker.io/obeedt/devops-ordenes:<SHA corto del commit>`

## Endpoints

| Método y ruta | Qué hace | Respuestas |
|---|---|---|
| `POST /ordenes` | Paga una reservación. Cuerpo: `reservacion_id`, `cliente_nombre`, `cliente_email` | `201` con la orden, el evento y sus asientos · `404` reservación · `409` con `motivo` (`ya_pagada` con su `orden_id`, `vencida`, `cancelada`) · `422` datos inválidos |
| `GET /ordenes/{id}` | Un pedido con su evento y asientos | `200` · `404` |
| `GET /health`, `/health/vivo`, `/metrics`, `/docs` | Igual que en todos los servicios | |

## El pago es una sola transacción

1. Bloquea la reservación (`FOR UPDATE`) y revisa que siga activa y sin vencer.
2. Toma sus asientos apartados y crea la orden; el **total lo calcula la base** con los precios de los asientos.
3. Pasa los asientos a `vendido` y la reservación a `confirmada`.

Si algo falla, no se guarda nada. Como `ordenes.reservacion_id` es único, una
reservación **no se puede cobrar dos veces**, aunque el navegador reintente:
el segundo intento recibe `409 ya_pagada` con el número de su orden.

Es el tramo **pedidos → BD** de la traza que pide la rúbrica (frontend →
pedidos → BD).

## Métricas

| Métrica | Qué mide |
|---|---|
| `http_requests_total`, `http_request_duration_seconds` | Peticiones por código y latencia |
| `boletera_ordenes_total{evento_id}` | Órdenes pagadas |
| `boletera_ventas_boletos_total{evento_id,seccion}` | Boletos vendidos por este servicio (ritmo de venta) |
| `boletera_ingresos_pesos_total{evento_id}` | Ingresos |
| `boletera_pagos_rechazados_total{motivo}` | Pagos rechazados: `ya_pagada`, `vencida`, `cancelada`, `sin_asientos` |

El total de boletos vendidos por evento (con la venta inicial) lo da
`svc-eventos` desde la base; aquí se mide lo vendido en vivo.

## Configuración

Igual que `svc-eventos`: `DB_*`, `ERROR_RATE`, `LOG_LEVEL`, `OTEL_*`.

## Pruebas

```bash
pip install -r requirements-dev.txt
python -m pytest
```
