#!/usr/bin/env python3

import argparse
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import urllib.error


OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
PG_TOOLS_URL = "http://127.0.0.1:8100/sales/query"

DEFAULT_MODEL = "ministral-3:14b"


SYSTEM_PROMPT = """
Eres el Asesor Gerencial IA de Grupo Amanecer.

Reasoning: low

Responde siempre en español, de forma clara, breve y profesional.

Para preguntas generales, responde normalmente.

Para información empresarial, utiliza las herramientas disponibles y no inventes datos.

Usa sales_query para consultas de ventas:
- operation="latest_confirmed" para la última orden confirmada.
- operation="sales_summary" para montos, cantidades o ventas por período.

Si una herramienta devuelve el campo "answer", utiliza ese contenido completo como respuesta principal y no omitas información.

Una orden con estado "sale" significa orden confirmada; no implica necesariamente que esté entregada, facturada o pagada.
""".strip()


SALES_TOOL = {
    "type": "function",
    "function": {
        "name": "sales_query",
        "description": (
            "Consulta información certificada de ventas de Grupo Amanecer. "
            "Permite consultar la última orden confirmada o un resumen de "
            "ventas por período, opcionalmente filtrado por sucursal."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": [
                        "latest_confirmed",
                        "sales_summary"
                    ],
                    "description": (
                        "Use latest_confirmed para la última orden confirmada. "
                        "Use sales_summary para montos o cantidades por período."
                    )
                },
                "warehouse": {
                    "type": "string",
                    "description": (
                        "Sucursal o warehouse opcional, por ejemplo "
                        "CARACAS, VALENCIA o CALABOZO."
                    )
                },
                "as_of": {
                    "type": "string",
                    "description": (
                        "Fecha/hora máxima ISO-8601 para latest_confirmed."
                    )
                },
                "date_from": {
                    "type": "string",
                    "description": (
                        "Inicio inclusivo del período ISO-8601 para sales_summary."
                    )
                },
                "date_to": {
                    "type": "string",
                    "description": (
                        "Fin exclusivo del período ISO-8601 para sales_summary."
                    )
                },
                "conversion_date": {
                    "type": "string",
                    "description": (
                        "Fecha YYYY-MM-DD usada para conversión VES a USD."
                    )
                }
            },
            "required": ["operation"]
        }
    }
}


def http_json(url, payload=None, headers=None, timeout=300):
    if headers is None:
        headers = {}

    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            **headers,
        }
        req = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method="POST",
        )
    else:
        req = urllib.request.Request(
            url,
            headers=headers,
            method="GET",
        )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return json.loads(body)

    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(
            f"\nHTTP ERROR {exc.code} llamando {url}\n{body}",
            file=sys.stderr,
        )
        raise

    except urllib.error.URLError as exc:
        print(
            f"\nERROR conectando con {url}: {exc}",
            file=sys.stderr,
        )
        raise


def ollama_chat(model, messages, tools=None):
    payload = {
        "model": model,
        "stream": False,
        "messages": messages,
        "keep_alive": "24h",
}

    if tools is not None:
        payload["tools"] = tools

    return http_json(
        OLLAMA_URL,
        payload=payload,
        timeout=600,
    )


def call_sales_query(api_key, arguments):
    allowed = {
        "operation",
        "warehouse",
        "as_of",
        "date_from",
        "date_to",
        "conversion_date",
    }

    params = {
        key: value
        for key, value in arguments.items()
        if key in allowed and value is not None
    }

    query = urllib.parse.urlencode(params)

    url = f"{PG_TOOLS_URL}?{query}"

    result = http_json(
        url,
        headers={
            "X-API-Key": api_key
        },
        timeout=120,
    )

    return result


def pretty_seconds(value):
    return f"{value:.2f} s"


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Prueba directa Ollama + sales_query "
            "sin pasar por Open WebUI."
        )
    )

    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Modelo Ollama. Default: {DEFAULT_MODEL}",
    )

    parser.add_argument(
        "question",
        nargs="*",
        help="Pregunta a realizar.",
    )

    args = parser.parse_args()

    api_key = os.environ.get("PG_TOOLS_API_KEY")

    if not api_key:
        print(
            "ERROR: PG_TOOLS_API_KEY no está cargada.\n\n"
            "Ejecuta antes:\n"
            "  set -a\n"
            "  source .env\n"
            "  set +a",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.question:
        question = " ".join(args.question)
    else:
        question = input("Pregunta: ").strip()

    if not question:
        print("ERROR: pregunta vacía", file=sys.stderr)
        sys.exit(1)

    model = args.model

    print()
    print("=" * 72)
    print("PRUEBA DIRECTA OLLAMA + PG-TOOLS")
    print("=" * 72)
    print(f"Modelo:   {model}")
    print(f"Pregunta: {question}")
    print("=" * 72)

    total_start = time.perf_counter()

    messages = [
        {
            "role": "system",
            "content": SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": question,
        },
    ]

    # -----------------------------------------------------
    # ETAPA 1
    # El modelo decide si necesita utilizar sales_query
    # -----------------------------------------------------

    decision_start = time.perf_counter()

    first_response = ollama_chat(
        model=model,
        messages=messages,
        tools=[SALES_TOOL],
    )

    decision_time = time.perf_counter() - decision_start

    assistant_message = first_response.get("message", {})
    tool_calls = assistant_message.get("tool_calls") or []

    print()
    print("ETAPA 1 — DECISIÓN DEL MODELO")
    print("-" * 72)
    print(f"Tiempo: {pretty_seconds(decision_time)}")

    if not tool_calls:
        content = assistant_message.get("content", "")

        print("El modelo NO llamó una herramienta.")
        print()
        print("RESPUESTA:")
        print(content)

        total_time = time.perf_counter() - total_start

        print()
        print("=" * 72)
        print("TIEMPOS")
        print("=" * 72)
        print(f"Modelo / respuesta directa: {pretty_seconds(decision_time)}")
        print(f"TOTAL:                     {pretty_seconds(total_time)}")
        return

    messages.append(assistant_message)

    total_tool_time = 0.0

    # -----------------------------------------------------
    # ETAPA 2
    # Ejecutar todas las llamadas solicitadas por el modelo
    # -----------------------------------------------------

    for index, tool_call in enumerate(tool_calls, start=1):
        function = tool_call.get("function", {})
        function_name = function.get("name")
        arguments = function.get("arguments", {})

        if isinstance(arguments, str):
            arguments = json.loads(arguments)

        print()
        print(f"TOOL CALL #{index}")
        print("-" * 72)
        print(f"Herramienta: {function_name}")
        print("Argumentos:")
        print(
            json.dumps(
                arguments,
                indent=2,
                ensure_ascii=False,
            )
        )

        if function_name != "sales_query":
            tool_result = {
                "error": (
                    f"Herramienta no soportada: {function_name}"
                )
            }

        else:
            tool_start = time.perf_counter()

            tool_result = call_sales_query(
                api_key,
                arguments,
            )

            tool_elapsed = time.perf_counter() - tool_start
            total_tool_time += tool_elapsed

            print(f"PG-TOOLS: {pretty_seconds(tool_elapsed)}")

        print()
        print("RESULTADO DEL TOOL:")
        print(
            json.dumps(
                tool_result,
                indent=2,
                ensure_ascii=False,
            )
        )

        messages.append(
            {
                "role": "tool",
                "content": json.dumps(
                    tool_result,
                    ensure_ascii=False,
                ),
            }
        )

    # -----------------------------------------------------
    # ETAPA 3
    # El modelo recibe el resultado del tool
    # y genera la respuesta final
    # -----------------------------------------------------

    final_start = time.perf_counter()

    final_response = ollama_chat(
        model=model,
        messages=messages,
        tools=[SALES_TOOL],
    )

    final_time = time.perf_counter() - final_start

    final_message = final_response.get("message", {})
    final_content = final_message.get("content", "")

    total_time = time.perf_counter() - total_start

    print()
    print("=" * 72)
    print("RESPUESTA FINAL")
    print("=" * 72)
    print(final_content)

    print()
    print("=" * 72)
    print("TIEMPOS")
    print("=" * 72)
    print(
        f"1. Decisión / tool calling: {pretty_seconds(decision_time)}"
    )
    print(
        f"2. PG-TOOLS:               {pretty_seconds(total_tool_time)}"
    )
    print(
        f"3. Respuesta final modelo: {pretty_seconds(final_time)}"
    )
    print("-" * 72)
    print(
        f"TOTAL:                     {pretty_seconds(total_time)}"
    )

    # Datos adicionales reportados por Ollama, si existen.
    load_duration = final_response.get("load_duration")
    prompt_eval_duration = final_response.get("prompt_eval_duration")
    eval_duration = final_response.get("eval_duration")
    eval_count = final_response.get("eval_count")

    if any(
        value is not None
        for value in (
            load_duration,
            prompt_eval_duration,
            eval_duration,
            eval_count,
        )
    ):
        print()
        print("=" * 72)
        print("MÉTRICAS OLLAMA")
        print("=" * 72)

        if load_duration is not None:
            print(
                f"load_duration:        "
                f"{load_duration / 1_000_000_000:.3f} s"
            )

        if prompt_eval_duration is not None:
            print(
                f"prompt_eval_duration: "
                f"{prompt_eval_duration / 1_000_000_000:.3f} s"
            )

        if eval_duration is not None:
            print(
                f"eval_duration:        "
                f"{eval_duration / 1_000_000_000:.3f} s"
            )

        if eval_count is not None:
            print(
                f"eval_count:           {eval_count}"
            )

        if (
            eval_duration
            and eval_count
        ):
            tokens_per_second = (
                eval_count /
                (eval_duration / 1_000_000_000)
            )

            print(
                f"generación:           "
                f"{tokens_per_second:.2f} tokens/s"
            )


if __name__ == "__main__":
    main()
