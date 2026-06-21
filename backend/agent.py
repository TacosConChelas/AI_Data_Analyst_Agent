"""
AI Data Analyst Agent – native function calling via Ollama.

This is a STATELESS reasoning engine: it owns only the LLM client and the
charts directory. Data (DataFrames + RAG) lives in datastore.py and the active
file + conversation history live in session.py. They are passed into chat().

Flow per user message:
  1. Build messages (system + recent history + user)
  2. Call LLM with tool definitions (first step forced via tool_choice)
  3. If the model returns tool_calls → execute each tool, append results, loop
  4. If the model returns plain text → return it as the final answer
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from openai import OpenAI

from tools import TOOL_DEFINITIONS, execute_tool

load_dotenv()

OLLAMA_BASE_URL   = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_MODEL      = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
MAX_HISTORY_TURNS = 8
MAX_TOOL_STEPS    = 10  # max tool-call rounds per question


class DataAnalystAgent:
    def __init__(self, charts_dir: Path | None = None):
        self.client = OpenAI(base_url=OLLAMA_BASE_URL, api_key="ollama")
        self.charts_dir = charts_dir or Path("../charts")
        self.charts_dir.mkdir(parents=True, exist_ok=True)
        self._check_ollama()

    # ──────────────────────────────────────────────────────
    def _check_ollama(self):
        try:
            names = [m.id for m in (self.client.models.list().data or [])]
            if OLLAMA_MODEL not in names:
                print(f"[WARN] '{OLLAMA_MODEL}' not in Ollama. Run: ollama pull {OLLAMA_MODEL}")
            else:
                print(f"[OK] Ollama ready – {OLLAMA_MODEL}")
        except Exception as exc:
            print(f"[WARN] Cannot reach Ollama: {exc}")

    # ──────────────────────────────────────────────────────
    # Chat – native function calling loop.
    #
    # df / filename / history / rag are supplied by the caller (server.py),
    # sourced from the shared DataStore and the per-session state. `history`
    # is the session's list and is mutated in place on a successful answer.
    # ──────────────────────────────────────────────────────
    def chat(
        self,
        message: str,
        *,
        df: pd.DataFrame | None,
        filename: str | None,
        history: list[dict],
        rag,
    ) -> dict:
        if df is None or not filename:
            return _empty("Please upload or select a CSV or Excel file first.")

        ctx_docs = rag.search(message, filename, n_results=4)
        context  = "\n\n---\n\n".join(ctx_docs) or "No specific context found."

        system_msg = {"role": "system", "content": _system_prompt(filename, df, context)}
        user_msg   = {"role": "user",   "content": message}

        recent = history[-(MAX_HISTORY_TURNS * 2):]
        messages: list[dict] = [system_msg] + recent + [user_msg]

        charts: list[str]  = []
        tables: list[dict] = []

        tool_choice = "required"  # force at least one tool call on the first step

        for step in range(MAX_TOOL_STEPS):
            try:
                resp = self.client.chat.completions.create(
                    model=OLLAMA_MODEL,
                    messages=messages,
                    tools=TOOL_DEFINITIONS,
                    tool_choice=tool_choice,
                    temperature=0.1,
                )
            except Exception as exc:
                return _empty(f"LLM error: {exc}")

            msg = resp.choices[0].message

            # ── After first tool call, allow the model to answer freely ──
            tool_choice = "auto"

            # ── No tool call → final answer ───────────────────────
            if not msg.tool_calls:
                answer_text = msg.content or ""
                history.append(user_msg)
                history.append({"role": "assistant", "content": answer_text})
                return {
                    "answer":    answer_text,
                    "logic":     "",
                    "insight":   "",
                    "charts":    charts,
                    "tables":    tables,
                    "full_text": answer_text,
                }

            # ── Append assistant message with tool_calls ──────────
            messages.append({
                "role":       "assistant",
                "content":    msg.content or "",
                "tool_calls": [
                    {
                        "id":       call.id,
                        "type":     "function",
                        "function": {
                            "name":      call.function.name,
                            "arguments": call.function.arguments,
                        },
                    }
                    for call in msg.tool_calls
                ],
            })

            # ── Execute each tool and append results ──────────────
            for call in msg.tool_calls:
                try:
                    inputs = json.loads(call.function.arguments)
                except json.JSONDecodeError:
                    inputs = {}

                result = execute_tool(
                    name=call.function.name,
                    inputs=inputs,
                    df=df,
                    charts_dir=self.charts_dir,
                    rag=rag,
                    active_file=filename,
                )

                if isinstance(result, dict):
                    if result.get("type") == "chart":
                        charts.append(result["filename"])
                    elif result.get("type") == "table":
                        tables.append({
                            "name":    result.get("table_name", "Table"),
                            "columns": result.get("columns", []),
                            "data":    result.get("data", []),
                        })

                messages.append({
                    "role":         "tool",
                    "tool_call_id": call.id,
                    "content":      json.dumps(result, default=str, ensure_ascii=False),
                })

        return _empty("Reached the maximum tool steps. Try rephrasing your question.")


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────
def _system_prompt(filename: str, df: pd.DataFrame, context: str) -> str:
    dtype_lines = "\n".join(
        f"  - {c}: {t} (nulls: {int(df[c].isnull().sum())})"
        for c, t in df.dtypes.items()
    )
    sample = df.head(3).to_string(max_colwidth=24)
    return f"""You are a Data Analyst AI. You MUST call a tool before answering. Never write analysis or describe data from memory.

STRICT RULES:
1. ALWAYS call a tool first. Never answer without calling a tool.
2. User asks for a table / list / filter / rows → call create_derived_table
3. User asks for a number / stat / aggregation → call run_pandas_query
4. User asks for a chart / graph / plot → call create_visualization
5. After the tool returns, summarize ONLY what the tool actually returned. Do NOT invent rows, names or numbers, and do NOT ask the user what they want.

## Dataset: {filename}
- {df.shape[0]} rows × {df.shape[1]} columns
- Columns (type, null count):
{dtype_lines}

## Sample rows (inspect the REAL value formats before writing code)
{sample}

## Context
{context}

## Data & code rules
- `df` is the full DataFrame, always available inside tool code.
- Look at the sample above for the real value format of each column.
- Datetime columns: filter a single year with `df[df['col'].dt.year == 2013]`.
- Drop missing values before aggregating or plotting with `.dropna()`.
- A pie/bar chart of a high-cardinality column cannot show hundreds of slices — take the top 10 by value and group the rest as 'Others'.
- For a pie OR bar chart of categories, index the data by the descriptive NAME column so each slice/bar is named, e.g. `df.set_index('title')['total_sales'].plot(kind='bar')` — never leave the default row-number index. The app adds a color→name legend automatically.
- EVERY chart (bar, pie, line, box/whisker, scatter, histogram, …) MUST include a legend (symbology). Give each plotted series a `label=` and call `ax.legend()`. For categorical bar/pie charts the legend must identify each category. The app adds a legend automatically if you forget, but prefer to add it yourself.
- create_derived_table: store result in `result_df`
- run_pandas_query: store result in `result`
- create_visualization: build it with `fig, ax = plt.subplots()`; do NOT call plt.show() or plt.savefig().
"""


def _empty(msg: str) -> dict:
    return {"answer": msg, "logic": "", "insight": "",
            "charts": [], "tables": [], "full_text": msg}
