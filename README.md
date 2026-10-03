# Osolight IA

Repositorio principal de proyectos de inteligencia artificial de Osolight.

## Proyectos

### Grupo Amanecer

Asesor Gerencial IA para consulta y análisis controlado de datos empresariales de Odoo.

Estructura principal:

- `amanecer/pg-ia-lab/` — Backend de herramientas empresariales
- `amanecer/open-webui/` — Configuración del frontend Open WebUI
- `amanecer/nginx/` — Publicación interna y personalización de interfaz
- `amanecer/docs/` — Arquitectura, operación, PROD/DEV y demos

## Principio de arquitectura

El modelo de lenguaje no consulta directamente la base de datos.

El flujo es:

Open WebUI
→ Asesor Gerencial IA
→ herramientas controladas
→ pg-tools / FastAPI
→ réplica PostgreSQL de Odoo

El backend es la fuente controlada de datos y las reglas de negocio permanecen fuera del modelo.
