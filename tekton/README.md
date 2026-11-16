# tekton

Pipeline de integración continua con **Tekton Pipelines** y **Tekton
Triggers**:

1. Clonar el repositorio.
2. Correr las pruebas.
3. Construir y publicar la imagen en Docker Hub con tag = SHA del commit.
4. Actualizar ese tag en el repo GitOps (Argo CD hace el resto).

Aquí irán las Tasks, el Pipeline, el TriggerTemplate y el EventListener que
recibe el webhook de GitHub. Las credenciales de Docker Hub y GitHub van en
Secrets creados fuera de Git.
