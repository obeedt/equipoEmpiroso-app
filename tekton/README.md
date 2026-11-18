# tekton

Pipeline de integración continua con **Tekton Pipelines v1.18.0** y **Tekton
Triggers v0.37.2**. Cada push a `main` de este repositorio:

1. clona el commit;
2. detecta qué servicios cambiaron (`git diff` contra el commit anterior);
3. corre sus pruebas (`pytest`);
4. construye su imagen con **Buildah** y la publica en Docker Hub con tag = **SHA corto del commit**;
5. cambia `newTag` en `equipoEmpiroso-gitops/tienda/<servicio>/kustomization.yaml` y hace push.

Argo CD ve ese commit y despliega la versión nueva: **Tekton construye, Argo CD
despliega**. Estos archivos también los aplica Argo CD (Application `ci` en el
repo GitOps); Tekton se instala desde `equipoEmpiroso-gitops/plataforma/tekton`.

## Archivos

| Archivo | Qué es |
|---|---|
| `00-namespace.yaml` | Namespace `ci` con política de seguridad `privileged` (Buildah la necesita) |
| `01-cuentas.yaml` | Cuentas `pipeline` (ejecuciones) y `disparador` (EventListener) con los roles de Triggers |
| `10-tarea-clonar.yaml` | Task: clona el repo en el commit del push |
| `11-tarea-detectar-cambios.yaml` | Task: qué servicios cambiaron y la etiqueta (SHA corto) |
| `12-tarea-pruebas.yaml` | Task: `pytest` de un servicio |
| `13-tarea-construir-imagen.yaml` | Task: `buildah build` + `buildah push` a Docker Hub |
| `14-tarea-actualizar-gitops.yaml` | Task: cambia `newTag` en el repo GitOps y hace push |
| `20-pipeline.yaml` | Pipeline `boletera-ci` que une las Tasks |
| `30-triggers.yaml` | TriggerBinding, TriggerTemplate y EventListener del webhook de GitHub |
| `31-servicio-webhook.yaml` | Service `webhook-github` en el puerto fijo 30880 (lo expone Tailscale Funnel) |
| `manual/ejecutar-todo.yaml` | PipelineRun manual que construye los 4 servicios (Argo CD no lo aplica) |

## Secrets (no están en Git)

Se crean **una vez**, a mano, en k8s-cp. Los valores se escriben o se generan
sin que queden en el historial ni en pantalla:

```bash
# Token de Docker Hub (Account settings → Personal access tokens → Read & Write)
read -rsp "Token de Docker Hub: " T; echo
kubectl -n ci create secret docker-registry dockerhub \
  --docker-server=https://index.docker.io/v1/ --docker-username=obeedt --docker-password="$T"; unset T

# Token de GitHub: fine-grained, SOLO equipoEmpiroso-gitops, permiso Contents: Read and write
read -rsp "Token de GitHub: " T; echo
kubectl -n ci create secret generic github-gitops --from-literal=token="$T"; unset T

# Secreto del webhook: se genera al azar y se muestra UNA vez para copiarlo en GitHub
S=$(head -c 32 /dev/urandom | base64 | tr -d '/+='); echo "$S"
kubectl -n ci create secret generic github-webhook --from-literal=secret="$S"; unset S
```

## Webhook: Tailscale Funnel + GitHub

El clúster no es accesible desde internet. **Tailscale Funnel** publica solo el
EventListener, con HTTPS, en `https://k8s-w3.tail4f16c9.ts.net`. En k8s-w3:

```bash
sudo tailscale funnel --bg --https=443 http://127.0.0.1:30880
```

`--bg` lo deja activo aunque se reinicie el nodo. La primera vez puede pedir
que se permita Funnel en la tailnet (abre un enlace de la consola de Tailscale).

En GitHub, **equipoEmpiroso-app → Settings → Webhooks → Add webhook**:

| Campo | Valor |
|---|---|
| Payload URL | `https://k8s-w3.tail4f16c9.ts.net` |
| Content type | `application/json` |
| Secret | el valor generado arriba |
| Eventos | *Just the push event* |

## Ejecución manual y seguimiento

```bash
kubectl create -f manual/ejecutar-todo.yaml        # los 4 servicios desde main
kubectl -n ci get pipelineruns
kubectl -n ci get taskruns -l tekton.dev/pipelineRun=<nombre>
```

También en el Tekton Dashboard (solo lectura): http://tekton.100.103.221.107.nip.io
