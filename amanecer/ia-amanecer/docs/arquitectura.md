# Arquitectura — Asesor Gerencial IA

## Objetivo

Permitir consultas en lenguaje natural sobre información empresarial de Grupo Amanecer sin permitir que el modelo de lenguaje acceda directamente a PostgreSQL.

## Flujo principal

    Usuario
      ↓
    Open WebUI
      ↓
    Asesor Gerencial IA
      ↓
    Modelo local vía Ollama
      ↓
    Herramientas controladas
      ├── sales_query
      └── sales_chart
            ↓
    pg-tools / FastAPI
      ↓
    PostgreSQL réplica Odoo

## Principios de diseño

1. El LLM no es la fuente de verdad.
2. El backend controla las reglas de negocio.
3. El modelo no ejecuta SQL directamente.
4. PostgreSQL se consulta con acceso de solo lectura.
5. Las operaciones deben tener contratos simples y verificables.
6. Los modelos pueden cambiar sin reescribir la lógica empresarial.
7. Los gráficos reutilizan datos obtenidos por las mismas operaciones certificadas del backend.

## Componentes

### Open WebUI

Interfaz principal para los usuarios.

Responsabilidades:

- interacción en lenguaje natural;
- presentación de resultados;
- presentación de gráficos;
- control de acceso a modelos y herramientas;
- experiencia diferenciada entre usuarios y administradores.

### Asesor Gerencial IA

Modelo configurado para interpretar la intención del usuario y seleccionar la operación empresarial adecuada.

No contiene la lógica principal de negocio.

### Ollama

Motor local de inferencia utilizado por el Asesor Gerencial IA.

El modelo puede ser sustituido sin modificar las reglas empresariales implementadas en el backend.

### pg-tools

API desarrollada con FastAPI.

Responsabilidades:

- validar parámetros;
- aplicar reglas empresariales;
- ejecutar consultas controladas;
- devolver respuestas estructuradas;
- generar gráficos a partir de operaciones certificadas;
- impedir acceso SQL arbitrario desde el modelo.

### PostgreSQL

Réplica de la base de datos de Odoo utilizada para consultas analíticas.

El acceso del Asesor Gerencial IA es de solo lectura.

## Regla principal

**Backend estable + contratos simples + modelo intercambiable.**

La lógica empresarial debe permanecer en el backend y no depender del comportamiento particular de un modelo de lenguaje.

## Metodología de validación

Cada nueva capacidad debe seguir:

    SQL de referencia
      ↓
    implementación
      ↓
    curl
      ↓
    comparación
      ↓
    aprobación
      ↓
    Open WebUI

Para datos variables se congela un identificador, fecha o punto de corte.

Para agregados se utilizan períodos cerrados cuando sea posible.
