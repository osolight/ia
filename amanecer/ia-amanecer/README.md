# IA Amanecer

Proyecto de Inteligencia Artificial empresarial para Grupo Amanecer.

## Componente principal

Asesor Gerencial IA.

Arquitectura general:

Usuario
→ Open WebUI
→ Asesor Gerencial IA
→ modelo local Ollama
→ herramientas controladas FastAPI
→ réplica PostgreSQL/Odoo de solo lectura

## Estructura

- `docs/` — arquitectura, reglas, UAT y documentación operativa.
- `pg-ia-lab/` — backend de herramientas y lógica empresarial.
- `open-webui/` — configuración e integración de Open WebUI.
- `deploy/` — configuración específica de DEV, PROD y proxy.
- `scripts/` — utilidades de despliegue y validación.

## Entornos

### DEV

- Open WebUI: puerto 3001
- pg-tools: puerto 8101
- Desarrollo y certificación.

### PROD

- Open WebUI: puerto 3000
- pg-tools: puerto 8100
- Solo funcionalidades certificadas.

## Regla de desarrollo

SQL de referencia
→ backend
→ curl
→ comparación
→ Open WebUI DEV
→ aprobación
→ PROD

## Principio fundamental

El LLM no es la fuente de verdad.

La lógica empresarial y las validaciones deben residir en el backend.
