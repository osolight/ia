#!/usr/bin/env python3

import argparse
import json
import os
import sys
import time

import test_model_sales as base


MAX_TOOL_ROUNDS = 8


def seconds(value):
    return f"{value:.2f} s"


def main():
    parser = argparse.ArgumentParser(
        description="Benchmark Ollama + PG-TOOLS con múltiples tool calls."
    )

    parser.add_argument(
        "--model",
        default="gpt-oss:20b",
    )

    parser.add_argument(
        "question",
        nargs="*",
    )

    args = parser.parse_args()

    api_key = os.environ.get("PG_TOOLS_API_KEY")

    if not api_key:
        print(
            "ERROR: PG_TOOLS_API_KEY no está cargada.",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.question:
        question = " ".join(args.question)
    else:
        question = input("Pregunta: ").strip()

    if not question:
        raise SystemExit("Pregunta vacía")

    print()
    print("=" * 72)
    print("BENCHMARK MULTI-TOOL")
    print("=" * 72)
    print(f"Modelo:   {args.model}")
    print(f"Pregunta: {question}")
    print("=" * 72)

    messages = [
        {
            "role": "system",
            "content": base.SYSTEM_PROMPT,
        },
        {
            "role": "user",
            "content": question,
        },
    ]

    total_start = time.perf_counter()

    total_model_time = 0.0
    total_tool_time = 0.0
    total_tool_calls = 0

    final_response = None

    for round_number in range(1, MAX_TOOL_ROUNDS + 1):

        model_start = time.perf_counter()

        response = base.ollama_chat(
            model=args.model,
            messages=messages,
            tools=[base.SALES_TOOL],
        )

        model_elapsed = time.perf_counter() - model_start
        total_model_time += model_elapsed

        message = response.get("message", {})
        content = message.get("content", "")
        tool_calls = message.get("tool_calls") or []

        print()
        print(
            f"RONDA {round_number} — MODELO: "
            f"{seconds(model_elapsed)}"
        )

        # -------------------------------------------------
        # Si no hay más tool calls, tenemos respuesta final
        # -------------------------------------------------

        if not tool_calls:
            final_response = response

            print("No solicitó más herramientas.")
            break

        print(
            f"Solicitó {len(tool_calls)} llamada(s) a herramienta."
        )

        messages.append(message)

        # -------------------------------------------------
        # Ejecutar todas las tools solicitadas en esta ronda
        # -------------------------------------------------

        for index, tool_call in enumerate(tool_calls, start=1):

            total_tool_calls += 1

            function = tool_call.get("function", {})

            function_name = function.get("name")
            arguments = function.get("arguments", {})

            if isinstance(arguments, str):
                arguments = json.loads(arguments)

            print()
            print(
                f"TOOL #{total_tool_calls} "
                f"(ronda {round_number}.{index})"
            )
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
                        f"Herramienta no soportada: "
                        f"{function_name}"
                    )
                }

                tool_elapsed = 0.0

            else:
                tool_start = time.perf_counter()

                try:
                    tool_result = base.call_sales_query(
                        api_key,
                        arguments,
                    )

                except Exception as exc:
                    tool_result = {
                        "error": str(exc)
                    }

                tool_elapsed = (
                    time.perf_counter() - tool_start
                )

                total_tool_time += tool_elapsed

            print(f"PG-TOOLS: {seconds(tool_elapsed)}")

            print("RESULTADO:")
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

    else:
        print()
        print(
            f"ERROR: se alcanzó MAX_TOOL_ROUNDS="
            f"{MAX_TOOL_ROUNDS}"
        )
        sys.exit(2)

    total_elapsed = time.perf_counter() - total_start

    final_message = (
        final_response.get("message", {})
        if final_response
        else {}
    )

    final_content = final_message.get("content", "")

    print()
    print("=" * 72)
    print("RESPUESTA FINAL")
    print("=" * 72)

    if final_content:
        print(final_content)
    else:
        print("[RESPUESTA VACÍA]")

    print()
    print("=" * 72)
    print("RESUMEN")
    print("=" * 72)
    print(f"Rondas modelo:       {round_number}")
    print(f"Tool calls totales:  {total_tool_calls}")
    print(f"Tiempo modelo:       {seconds(total_model_time)}")
    print(f"Tiempo PG-TOOLS:     {seconds(total_tool_time)}")
    print("-" * 72)
    print(f"TOTAL:               {seconds(total_elapsed)}")

    # -----------------------------------------------------
    # Métricas de la última generación
    # -----------------------------------------------------

    if final_response:

        eval_duration = final_response.get("eval_duration")
        eval_count = final_response.get("eval_count")

        if eval_duration and eval_count:
            tok_s = (
                eval_count /
                (eval_duration / 1_000_000_000)
            )

            print(
                f"Generación final:    {tok_s:.2f} tokens/s"
            )


if __name__ == "__main__":
    main()
