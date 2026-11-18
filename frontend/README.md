# frontend

El sitio web de la boletera: **catálogo → carrito → pedido**. Páginas HTML
generadas en el servidor (FastAPI + Jinja2) que llaman a los otros tres
servicios por HTTP. No usa la base de datos.

Imagen: `docker.io/obeedt/devops-frontend:<SHA corto del commit>`
En el clúster: **http://tienda.100.103.221.107.nip.io** (desde la tailnet).

## Páginas

| Ruta | Qué muestra | Llama a |
|---|---|---|
| `GET /` | Catálogo con filtros por ciudad y género; "¡Últimos boletos!" arriba del 90 % | svc-eventos |
| `GET /evento/{id}` | Secciones con precio y lugares; formulario de sección y cantidad | svc-eventos |
| `POST /carrito` | Aparta los mejores lugares y lleva al carrito | svc-reservaciones |
| `GET /carrito` | Asientos, total, cuenta regresiva y formulario de pago | svc-reservaciones, svc-eventos |
| `POST /carrito/cancelar` | Libera los lugares | svc-reservaciones |
| `POST /pagar` | Paga y lleva a la confirmación | svc-ordenes |
| `GET /orden/{id}` | Confirmación del pedido | svc-ordenes |
| `GET /health`, `/health/vivo`, `/metrics` | Salud y métricas | — |

El carrito vive en dos cookies: `sesion` (identifica el navegador) y
`reservacion` (la reservación activa).

## Trazas y errores

Las llamadas a los otros servicios van instrumentadas con OpenTelemetry
(httpx) y llevan el encabezado `traceparent`. El servicio que la recibe
continúa **la misma traza**: frontend → svc-ordenes → PostgreSQL.

Si un servicio no responde o responde 5xx, la página dice "Algo salió mal" con
un **folio de error**, que es el `trace_id` de la petición. Con ese folio se
encuentra la traza en Jaeger y su log de error (demo 3).

`/health` no depende de los otros servicios: si uno se cae, la tienda sigue en
el Ingress y muestra su página de error en vez de desaparecer.

## Métricas

| Métrica | Qué mide |
|---|---|
| `http_requests_total`, `http_request_duration_seconds` | Peticiones por código y latencia |
| `boletera_llamadas_backend_total{servicio,resultado}` | Llamadas a cada servicio: `ok`, `error_5xx`, `sin_respuesta` |

## Configuración

| Variable | Valor en el clúster |
|---|---|
| `EVENTOS_URL`, `RESERVACIONES_URL`, `ORDENES_URL` | `http://svc-eventos`, `http://svc-reservaciones`, `http://svc-ordenes` |
| `TIMEOUT_SEGUNDOS` | `5` |
| `ERROR_RATE` | 0 a 1: fracción de páginas que fallan a propósito (demo 3) |
| `BANNER` | Aviso arriba de todas las páginas (vacío = sin aviso) |
| `LOG_LEVEL`, `OTEL_SERVICE_NAME`, `OTEL_EXPORTER_OTLP_ENDPOINT` | Igual que en los otros servicios |

## Pruebas

```bash
pip install -r requirements-dev.txt
python -m pytest
```

Los otros servicios se reemplazan con `httpx.MockTransport`.
