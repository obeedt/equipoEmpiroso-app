# svc-eventos

Catálogo de conciertos de la boletera (solo lectura). Expone la métrica de
negocio del proyecto 3: **boletos vendidos por evento**.

Imagen: `docker.io/obeedt/devops-eventos:<SHA corto del commit>`

## Endpoints

| Método y ruta | Qué hace |
|---|---|
| `GET /eventos` | Conciertos a la venta, por fecha. Filtros opcionales: `?ciudad=` y `?genero=` |
| `GET /eventos/{id}` | Un concierto con sus secciones: precio, total y lugares disponibles |
| `GET /health` | Listo para recibir tráfico (revisa la base) → `readinessProbe` |
| `GET /health/vivo` | El proceso responde → `livenessProbe` |
| `GET /metrics` | Métricas para Prometheus |
| `GET /docs` | Documentación interactiva (la genera FastAPI) |

## Métricas

| Métrica | Qué mide |
|---|---|
| `http_requests_total{method,route,status}` | Peticiones por código HTTP (la ruta es la plantilla, p. ej. `/eventos/{evento_id}`) |
| `http_request_duration_seconds` | Latencia (histograma) |
| `boletera_boletos_vendidos{evento_id,evento,artista,recinto}` | **Métrica de negocio**: boletos vendidos por evento |
| `boletera_porcentaje_vendido{...}` | Porcentaje del aforo vendido: la **alerta de negocio** es `> 90` |
| `boletera_capacidad`, `boletera_boletos_disponibles` | Aforo y lugares libres |
| `boletera_metricas_negocio_ok` | 1 si las métricas de negocio se pudieron leer de la base |
| `boletera_info{servicio,version}` | Versión desplegada (SHA del commit) |

Las de negocio se calculan en cada lectura de `/metrics` a partir de la vista
`ventas_por_evento` (~130 ms para los 22 eventos).

## Configuración (variables de entorno)

| Variable | Valor en el clúster |
|---|---|
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER` | `postgres`, `5432`, `boletera`, `boletera_app` |
| `DB_PASSWORD` | Secret `postgres-app` |
| `DB_POOL_MAX` | Conexiones máximas por réplica (5) |
| `ERROR_RATE` | 0 a 1: fracción de peticiones que fallan con 500 a propósito (demo 3) |
| `LOG_LEVEL` | `INFO` |
| `OTEL_SERVICE_NAME` | `svc-eventos` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | Colector de Jaeger (etapa de logs y trazas); vacío = no envía trazas |

## Logs

Una línea JSON por petición en stdout, con `nivel`, `servicio`, `mensaje`,
`trace_id` y `span_id`. Con el `trace_id` se pasa del log a su traza en Jaeger.

## Pruebas

```bash
pip install -r requirements-dev.txt
python -m pytest
```

No necesitan base de datos: un repositorio falso reemplaza a PostgreSQL.


MODIFICACIÓN DE PRUEBA DE TEKTON
