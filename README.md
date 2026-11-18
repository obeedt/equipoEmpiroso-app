# equipoEmpiroso-app

Código de la **boletera de conciertos** · Proyecto 3.

Cuatro microservicios en Python (FastAPI), cada uno con su propio
Containerfile e imagen en Docker Hub, y el pipeline de Tekton que los
construye.

Los manifiestos para desplegarlos en Kubernetes están en
[equipoEmpiroso-gitops](https://github.com/obeedt/equipoEmpiroso-gitops).

La tienda en el clúster: **http://tienda.100.103.221.107.nip.io** (desde la tailnet).

## Estructura

| Carpeta | Qué es | Imagen en Docker Hub |
|---|---|---|
| [`frontend/`](frontend/) | Sitio web: catálogo, carrito y pago (FastAPI + Jinja2) | `obeedt/devops-frontend` |
| [`svc-eventos/`](svc-eventos/) | Catálogo de conciertos y métrica de negocio | `obeedt/devops-eventos` |
| [`svc-reservaciones/`](svc-reservaciones/) | Carrito: reservación de asientos con bloqueo temporal (servicio distintivo) | `obeedt/devops-reservaciones` |
| [`svc-ordenes/`](svc-ordenes/) | Pago de reservaciones y pedidos | `obeedt/devops-ordenes` |
| [`tekton/`](tekton/) | Pipeline de CI: pruebas, construcción y publicación de imágenes | — |

Cada imagen se etiqueta con el **SHA corto del commit**. Cada carpeta tiene su
`README.md` con endpoints, métricas y configuración. La documentación completa
de la etapa está en
[equipoEmpiroso-gitops/docs/05-microservicios.md](https://github.com/obeedt/equipoEmpiroso-gitops/blob/main/docs/05-microservicios.md).

## Pruebas

En cada carpeta:

```bash
pip install -r requirements-dev.txt
python -m pytest
```

50 pruebas en total (10 + 15 + 12 + 13), sin base de datos ni otros servicios.

## Flujo

`git push` → Tekton prueba y construye la imagen (tag = SHA del commit) →
la publica en Docker Hub → actualiza el tag en el repo GitOps → Argo CD la
despliega.

## Requisitos de cada microservicio

- Endpoint `/health` (para las probes de Kubernetes) y `/metrics` (para Prometheus).
- Logs en JSON por stdout, con nivel, nombre del servicio y `trace_id`.
- Trazas con OpenTelemetry, enviadas por OTLP.
- Carpeta `tests/` con sus pruebas.
