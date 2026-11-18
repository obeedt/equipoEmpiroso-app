# svc-reservaciones

El **carrito** de la boletera y servicio distintivo del proyecto 3:
**reservación de asientos con bloqueo temporal**. El usuario elige sección y
cantidad; el servicio aparta los mejores lugares disponibles por unos minutos.
Si no se pagan a tiempo, se liberan solos.

Imagen: `docker.io/obeedt/devops-reservaciones:<SHA corto del commit>`

## Endpoints

| Método y ruta | Qué hace | Respuestas |
|---|---|---|
| `POST /reservaciones` | Aparta lugares. Cuerpo: `evento_id`, `seccion` (`VIP`, `Preferente`, `General`), `cantidad` (1 a 10), `sesion` | `201` con los asientos, el total y `expira_en` · `404` evento · `409` sin lugares (dice cuántos quedan) · `422` datos inválidos |
| `GET /reservaciones/{id}` | Consulta una reservación con sus asientos | `200` · `404` |
| `DELETE /reservaciones/{id}` | La cancela y libera sus asientos | `200` con los asientos liberados · `409` si ya se pagó |
| `GET /health`, `/health/vivo`, `/metrics`, `/docs` | Igual que en todos los servicios | |

## Cómo aparta los lugares

En una sola sentencia, con `FOR UPDATE SKIP LOCKED` (ver el contrato de datos
en `equipoEmpiroso-gitops/tienda/README.md`):

- toma los mejores lugares libres de la sección (o con un bloqueo ya vencido);
- dos compras simultáneas reciben lugares **distintos** y nunca el mismo asiento;
- si no alcanzan, no aparta ninguno (todo o nada) y responde `409`.

Cada `LIBERADOR_SEGUNDOS`, una tarea de fondo marca como `expirada` cada
reservación sin pagar que ya venció y libera sus asientos.

## Métricas

| Métrica | Qué mide |
|---|---|
| `http_requests_total`, `http_request_duration_seconds` | Peticiones por código y latencia |
| `boletera_reservaciones_total{resultado}` | Intentos: `creada`, `sin_lugares`, `evento_no_encontrado` |
| `boletera_boletos_bloqueados_total{evento_id,seccion}` | Boletos apartados |
| `boletera_reservaciones_canceladas_total` | Canceladas por el usuario |
| `boletera_reservaciones_vencidas_total` | Vencidas sin pagar |

## Configuración

Igual que `svc-eventos` (`DB_*`, `ERROR_RATE`, `LOG_LEVEL`, `OTEL_*`), más:

| Variable | Valor | Para qué |
|---|---|---|
| `RESERVA_MINUTOS` | `10` | Cuánto tiempo quedan apartados los lugares |
| `LIBERADOR_SEGUNDOS` | `60` | Cada cuánto se liberan las reservaciones vencidas (`0` = nunca) |

## Pruebas

```bash
pip install -r requirements-dev.txt
python -m pytest
```
