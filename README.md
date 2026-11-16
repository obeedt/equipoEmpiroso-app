# equipoEmpiroso-app

Código de la **boletera de conciertos** · Proyecto 3.

Cuatro microservicios en Python (FastAPI), cada uno con su propio
Containerfile e imagen en Docker Hub, y el pipeline de Tekton que los
construye.

Los manifiestos para desplegarlos en Kubernetes están en
[equipoEmpiroso-gitops](https://github.com/obeedt/equipoEmpiroso-gitops).

## Estructura

| Carpeta | Qué va ahí |
|---|---|
| [`frontend/`](frontend/) | Sitio web de la boletera |
| [`svc-eventos/`](svc-eventos/) | Catálogo de conciertos y boletos |
| [`svc-ordenes/`](svc-ordenes/) | Órdenes de compra |
| [`svc-reservaciones/`](svc-reservaciones/) | Reservación de asientos con bloqueo temporal (servicio distintivo) |
| [`tekton/`](tekton/) | Pipeline de CI: pruebas, construcción y publicación de imágenes |

## Flujo

`git push` → Tekton prueba y construye la imagen (tag = SHA del commit) →
la publica en Docker Hub → actualiza el tag en el repo GitOps → Argo CD la
despliega.

## Requisitos de cada microservicio

- Endpoint `/health` (para las probes de Kubernetes) y `/metrics` (para Prometheus).
- Logs en JSON por stdout, con nivel, nombre del servicio y `trace_id`.
- Trazas con OpenTelemetry, enviadas por OTLP.
- Carpeta `tests/` con sus pruebas.
