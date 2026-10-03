#pg-tools: Herramienta de consulta PostgreSQL para Open WebUI
#Pega este código en: Workspace → Tools → + New Tool

#Configura PG_TOOLS_URL con la IP de tu servidor.
"""

import json
import urllib.request
import urllib.error
from pydantic import BaseModel, Field

PG_TOOLS_URL = "http://10.17.71.19:8100"  # ← Cambia a la IP de tu servidor


class Tools:
    class Valves(BaseModel):
        pg_tools_url: str = Field(
            default=PG_TOOLS_URL, description="URL base del servidor pg-tools"
        )

    def __init__(self):
        self.valves = self.Valves()

    def _get(self, path: str) -> dict:
        url = f"{self.valves.pg_tools_url}{path}"
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())

    def _post(self, path: str, body: dict) -> dict:
        url = f"{self.valves.pg_tools_url}{path}"
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())

    def get_database_schema(self, schema_name: str = "public") -> str:
        """
        OBLIGATORIO usar esta herramienta cuando el usuario pregunte por:
        tablas, columnas, esquema, estructura de base de datos, modelos de Odoo,
        tablas principales, relaciones o cantidad estimada de filas.

        Esta herramienta NO es una función SQL. No debes generar SQL como:
        SELECT * FROM get_database_schema(...)

        Esta herramienta llama a la API externa pg-tools y devuelve directamente
        el esquema real de PostgreSQL.

        El resultado contiene líneas con este formato:
        - nombre_tabla (N filas): columna1 (tipo), columna2 (tipo)

        Si el usuario pide las primeras tablas, copia directamente las primeras
        líneas devueltas por esta herramienta. No inventes tablas. No respondas
        con conocimiento general de Odoo si esta herramienta devuelve datos.

        :param schema_name: Nombre del schema de PostgreSQL, normalmente public.
        :return: Lista real de tablas, columnas y cantidad estimada de filas.
        """
        try:
            data = self._get(f"/schema?schema_name={schema_name}")
            # Formatear de forma compacta para el contexto del LLM
            result = []
            for table, info in data["tables"].items():
                cols = ", ".join(
                    f"{c['name']} ({c['type']}{'  PK' if c['constraint'] == 'PRIMARY KEY' else ''})"
                    for c in info["columns"]
                )
                fks = ""
                if info.get("foreign_keys"):
                    fks = " | FK: " + ", ".join(
                        f"{fk['column']}→{fk['references']}"
                        for fk in info["foreign_keys"]
                    )
                result.append(
                    f"- {table} ({info.get('row_count', '?')} filas): {cols}{fks}"
                )
            return "\n".join(result)
        except urllib.error.HTTPError as e:
            return f"Error al obtener esquema: HTTP {e.code}"
        except Exception as e:
            return f"Error: {str(e)}"

    def run_sql_query(self, query: str, limit: int = 200) -> str:
        """
        Ejecuta una consulta SQL real en PostgreSQL usando pg-tools.

        Usa esta herramienta cuando el usuario pida datos reales, conteos,
        clientes, productos, facturas, ventas, inventario, usuarios, contactos
        o cualquier consulta sobre registros.

        IMPORTANTE:
        - get_database_schema NO es una función SQL.
        - No generes SQL tipo SELECT * FROM get_database_schema(...).
        - Para ver tablas usa get_database_schema.
        - Para contar registros usa SQL real, por ejemplo:
          SELECT COUNT(*) FROM res_partner;

        Solo se permiten SELECT, WITH o EXPLAIN.

        :param query: Consulta SQL válida para PostgreSQL.
        :param limit: Número máximo de filas a devolver.
        :return: Resultado real de PostgreSQL.
        """
        try:
            data = self._post("/query", {"query": query, "limit": min(limit, 500)})
            rows = data.get("rows", [])
            columns = data.get("columns", [])
            count = data.get("count", 0)

            if not rows:
                return f"La consulta no devolvió resultados. SQL ejecutado: {data.get('sql', query)}"

            # Formatear como tabla de texto para el LLM
            header = " | ".join(columns)
            separator = "-" * len(header)
            lines = [
                f"Resultados: {count} fila(s) | SQL: {data.get('sql', query)}",
                separator,
                header,
                separator,
            ]
            for row in rows[:50]:  # Máximo 50 filas en el contexto
                lines.append(
                    " | ".join(
                        str(v) if v is not None else "NULL" for v in row.values()
                    )
                )

            if count > 50:
                lines.append(f"... ({count - 50} filas más no mostradas)")

            return "\n".join(lines)
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            try:
                detail = json.loads(body).get("detail", body)
            except Exception:
                detail = body
            return f"Error SQL: {detail}"
        except Exception as e:
            return f"Error: {str(e)}"

    def get_table_sample(
        self, table_name: str, schema_name: str = "public", limit: int = 5
    ) -> str:
        """
        Obtiene una muestra de filas de una tabla para entender su contenido real.
        Útil para ver ejemplos de datos antes de hacer consultas más complejas.

        :param table_name: Nombre de la tabla
        :param schema_name: Schema de PostgreSQL (por defecto 'public')
        :param limit: Número de filas de muestra (por defecto 5)
        :return: Muestra de filas en formato texto
        """
        try:
            data = self._get(
                f"/sample/{table_name}?schema_name={schema_name}&limit={limit}"
            )
            rows = data.get("rows", [])
            columns = data.get("columns", [])
            if not rows:
                return f"La tabla '{table_name}' está vacía."
            lines = [f"Muestra de '{table_name}' ({len(rows)} filas):"]
            for row in rows:
                lines.append(str(dict(row)))
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {str(e)}"

    def get_numeric_stats(self, table_name: str, schema_name: str = "public") -> str:
        """
        Obtiene estadísticas de columnas numéricas de una tabla:
        mínimo, máximo, promedio y cantidad de valores nulos.
        Útil para entender rangos de datos y calcular KPIs básicos.

        :param table_name: Nombre de la tabla
        :param schema_name: Schema de PostgreSQL (por defecto 'public')
        :return: Estadísticas numéricas en formato texto
        """
        try:
            data = self._get(f"/stats/{table_name}?schema_name={schema_name}")
            stats = data.get("numeric_stats", {})
            if not stats:
                return data.get("message", "Sin columnas numéricas.")
            lines = [f"Estadísticas numéricas de '{table_name}':"]
            for col, s in stats.items():
                lines.append(
                    f"  {col}: min={s['min']} | max={s['max']} | "
                    f"avg={round(float(s['avg']), 2) if s['avg'] else 'N/A'} | "
                    f"nulls={s['nulls']}/{s['total']}"
                )
            return "\n".join(lines)
        except Exception as e:
            return f"Error: {str(e)}"
