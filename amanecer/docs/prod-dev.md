# Entornos PROD y DEV

## Objetivo

Mantener separados el entorno productivo y el entorno de desarrollo para poder probar cambios sin afectar a los usuarios.

## PROD

Runtime:

    /opt/ia-amanecer/pg-ia-lab
    /opt/ia-amanecer/open-webui

Servicios:

    Open WebUI :3000
    pg-tools   :8100

Dominio:

    asesor.grupo-amanecer.com

Uso:

- entorno productivo;
- acceso de usuarios finales;
- backend certificado;
- interfaz simplificada;
- herramientas técnicas ocultas para usuarios normales.

## DEV

Runtime:

    /opt/ia-amanecer/dev/pg-ia-lab
    /opt/ia-amanecer/dev/open-webui

Servicios:

    Open WebUI :3001
    pg-tools   :8101

Dominio:

    asesor-dev.grupo-amanecer.com

Uso:

- desarrollo;
- pruebas funcionales;
- validación de nuevas operaciones;
- pruebas de prompts;
- validación de gráficos;
- depuración técnica.

## Ollama

Ambos entornos utilizan el mismo servicio local de Ollama:

    127.0.0.1:11434

La separación entre PROD y DEV se mantiene en:

- Open WebUI;
- pg-tools;
- puertos;
- claves API;
- configuración;
- contenedores;
- integración entre frontend y backend.

## PostgreSQL

PROD y DEV consultan la réplica de Odoo mediante acceso de solo lectura.

El modelo de lenguaje no se conecta directamente a PostgreSQL.

## Flujo de promoción

Una funcionalidad nueva debe seguir:

    desarrollo en DEV
      ↓
    SQL de referencia
      ↓
    implementación backend
      ↓
    prueba con curl
      ↓
    comparación de resultados
      ↓
    prueba en Open WebUI DEV
      ↓
    aprobación
      ↓
    paso a PROD

## Regla operativa

No se desarrollan funcionalidades directamente sobre PROD.

Los cambios deben validarse primero en DEV y promoverse únicamente después de comprobar que mantienen los resultados esperados.
