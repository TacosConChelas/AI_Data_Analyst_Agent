"""
Tool definitions (OpenAI / Ollama format) and implementations.
"""
import ast
import builtins as _builtins
import json
import sys
import uuid
import traceback
import pandas as pd
import numpy as np
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import seaborn as sns

# ─────────────────────────────────────────────────────────────────────────────
# Tool schemas – OpenAI function-calling format (Ollama compatible)
# ─────────────────────────────────────────────────────────────────────────────
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "analyze_data",
            "description": (
                "Analyze the loaded CSV/Excel data. Returns schema, statistics, "
                "sample rows, missing values, or unique-value summaries."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query_type": {
                        "type": "string",
                        "enum": ["schema", "statistics", "sample",
                                 "null_check", "unique_values", "all"],
                        "description": (
                            "'schema' for column types, 'statistics' for numeric stats, "
                            "'sample' for preview rows, 'null_check' for missing values, "
                            "'unique_values' for distinct values, 'all' for everything."
                        ),
                    },
                    "columns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional: restrict analysis to these columns.",
                    },
                },
                "required": ["query_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_pandas_query",
            "description": (
                "Execute Python/pandas code on the loaded DataFrame. "
                "The variable 'df' holds the DataFrame. "
                "Store the final answer in a variable called 'result'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": (
                            "Python code. 'df' is the DataFrame. Store result in 'result'.\n"
                            "Examples:\n"
                            "  result = df['sales'].mean()\n"
                            "  result = df.groupby('product')['revenue'].sum()"
                            ".sort_values(ascending=False).head(5)"
                        ),
                    }
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_visualization",
            "description": (
                "Create a chart from the data using matplotlib/seaborn. "
                "Do NOT call plt.show() or plt.savefig() – the system saves the chart. "
                "Returns a chart filename rendered in the UI."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "chart_type": {
                        "type": "string",
                        "enum": ["bar", "pie", "line", "scatter",
                                 "histogram", "heatmap", "box", "area"],
                        "description": "Visual type of the chart.",
                    },
                    "code": {
                        "type": "string",
                        "description": (
                            "matplotlib/seaborn code. 'df', 'plt', 'sns', 'pd', 'np' available.\n"
                            "Do NOT call plt.show() or plt.savefig().\n"
                            "Example:\n"
                            "  fig, ax = plt.subplots(figsize=(10, 6))\n"
                            "  df.groupby('category')['sales'].sum().plot(kind='bar', ax=ax)\n"
                            "  ax.set_title('Sales by Category')\n"
                            "  plt.tight_layout()"
                        ),
                    },
                    "title": {
                        "type": "string",
                        "description": "Human-readable title for the chart.",
                    },
                },
                "required": ["chart_type", "code", "title"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_derived_table",
            "description": (
                "Create a new derived DataFrame from the existing data "
                "(aggregations, rankings, pivots, filtered views). "
                "Store the result in 'result_df'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": (
                            "Pandas code. 'df' is the original DataFrame. "
                            "Store result in 'result_df'.\n"
                            "Example:\n"
                            "  result_df = (df.groupby('year')['sales']\n"
                            "               .sum().reset_index()\n"
                            "               .sort_values('sales', ascending=False))"
                        ),
                    },
                    "table_name": {
                        "type": "string",
                        "description": "Descriptive name for the derived table.",
                    },
                },
                "required": ["code", "table_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_context",
            "description": (
                "Search the RAG knowledge base for relevant context about "
                "the dataset – column meanings, distributions, domain knowledge."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural-language search query.",
                    }
                },
                "required": ["query"],
            },
        },
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Restricted execution
#
# NOTE: This is a pragmatic mitigation for a LOCAL, single-user tool — not a
# true sandbox. It blocks the obvious escape routes (imports, dunder access,
# dangerous builtins) so a hallucinated or prompt-injected snippet cannot
# `import os; os.system(...)` or reach `__builtins__` via object internals.
# For untrusted / multi-tenant use, run tool code in an isolated subprocess
# or container instead (e.g. RestrictedPython, nsjail, gVisor).
# ─────────────────────────────────────────────────────────────────────────────
_SAFE_BUILTIN_NAMES = {
    "abs", "all", "any", "bool", "dict", "divmod", "enumerate", "filter",
    "float", "format", "frozenset", "int", "len", "list", "map", "max", "min",
    "print", "range", "repr", "reversed", "round", "set", "slice", "sorted",
    "str", "sum", "tuple", "zip", "isinstance", "issubclass",
}
_SAFE_BUILTINS = {
    n: getattr(_builtins, n) for n in _SAFE_BUILTIN_NAMES if hasattr(_builtins, n)
}

_FORBIDDEN_NAMES = {
    "eval", "exec", "compile", "open", "input", "__import__", "globals",
    "locals", "vars", "getattr", "setattr", "delattr", "memoryview",
    "breakpoint", "exit", "quit", "help",
}


def _check_code_safety(code: str) -> None:
    """Reject code that imports, reaches dunder internals, or calls a
    dangerous builtin. Raises ValueError with a hint the model can act on."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        raise ValueError(f"Invalid Python syntax: {exc}") from exc

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise ValueError(
                "imports are not allowed — 'df', 'pd', 'np', 'plt' and 'sns' "
                "are already available, use them directly."
            )
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise ValueError(f"access to dunder attribute '{node.attr}' is not allowed.")
        if isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            raise ValueError(f"use of '{node.id}' is not allowed.")


def _safe_exec(code: str, df: pd.DataFrame) -> dict:
    _check_code_safety(code)
    plt.close("all")
    namespace = {
        "__builtins__": _SAFE_BUILTINS,
        "df": df.copy(),
        "pd": pd, "np": np,
        "plt": plt, "sns": sns,
        "result": None, "result_df": None,
    }
    exec(code, namespace)  # noqa: S102
    return namespace


# ─────────────────────────────────────────────────────────────────────────────
# Pie-chart readability guardrail
#
# Small models often plot every category, producing an unreadable pie with
# hundreds of tiny slices. We wrap Axes.pie so any pie with more than
# _MAX_PIE_SLICES values is collapsed to the top slices by value plus an
# aggregated "Others" slice — deterministically, regardless of the code the
# model wrote. Patching Axes.pie also covers pandas Series.plot.pie /
# DataFrame.plot(kind="pie"), which call ax.pie() internally.
# ─────────────────────────────────────────────────────────────────────────────
_MAX_PIE_SLICES = 12  # keep the top N by value; the rest become one "Others" slice


def _stash_pie_symbology(ax, result, labels):
    """Hide the on-slice name labels and stash (wedges, labels) on the axes.
    The legend itself is added later by _apply_pie_legends — placing it AFTER
    the model's code runs (once aspect='equal' is set) avoids the overlap you
    get if the legend is added mid-draw."""
    try:
        wedges = result[0]
        if not labels or len(labels) != len(wedges):
            return
        for txt in (result[1] if len(result) > 1 else []):
            txt.set_visible(False)   # declutter: names go to the legend instead
        ax._pie_symbology = (list(wedges), [str(x) for x in labels])
    except Exception:
        pass


def _cmap(n):
    import matplotlib
    name = "tab20" if n > 10 else "tab10"
    try:
        return matplotlib.colormaps[name]
    except Exception:                       # older matplotlib
        return plt.get_cmap(name)


_NAME_COL_RE = r"(^|_)(name|title|label|product|category|item)"


def _resolve_category_names(df, labels):
    """If category labels look like integer row indices (because the model
    plotted a Series without naming its index), map them to a human-readable
    name column (title/name/product/…) so charts show names, not row numbers.
    Returns labels unchanged when that doesn't clearly apply."""
    import re
    try:
        idxs = [int(str(x)) for x in labels]      # fails if any label isn't int-like
    except (ValueError, TypeError):
        return labels
    if df is None or not set(idxs).issubset(set(df.index)):
        return labels
    candidates = [
        c for c in df.columns
        if re.search(_NAME_COL_RE, str(c), re.I)
        and not pd.api.types.is_numeric_dtype(df[c])
        and not pd.api.types.is_datetime64_any_dtype(df[c])
    ]
    if not candidates:
        return labels
    name_col = max(candidates, key=lambda c: df[c].nunique())
    out = []
    for i in idxs:
        try:
            s = str(df.at[i, name_col])
        except Exception:
            return labels
        out.append(s[:24] + "…" if len(s) > 25 else s)
    return out


def _legend_for_bars(ax, df=None) -> bool:
    """Single-series bar chart → colour each bar by its category and add a
    category→colour legend. Returns True if a legend was added."""
    import matplotlib.container as mcontainer
    import matplotlib.patches as mpatches

    bars = [c for c in ax.containers if isinstance(c, mcontainer.BarContainer)]
    if len(bars) != 1:
        return False                        # 0 or multi-series → handled elsewhere
    patches = list(bars[0])
    n = len(patches)
    if n == 0 or n > 20:                     # too many bars → x-axis labels suffice
        return False

    ticks = [t.get_text() for t in ax.get_xticklabels()]
    labels = ticks if sum(bool(t) for t in ticks) == n else [str(i + 1) for i in range(n)]
    labels = _resolve_category_names(df, labels)
    cmap = _cmap(n)
    handles = []
    for i, patch in enumerate(patches):
        color = cmap(i % getattr(cmap, "N", 10))
        patch.set_color(color)
        handles.append(mpatches.Patch(color=color, label=str(labels[i])))
    ax.legend(handles=handles, loc="center left",
              bbox_to_anchor=(1.0, 0.5), fontsize="small")
    return True


def _cap_bar_axes(ax, df=None):
    """Single-series vertical bar chart with too many bars → keep the top-N by
    height and aggregate the rest into one 'Others' bar (redraws the axes).
    Parallels the pie cap so bar charts stay legible and can carry a clean
    category legend."""
    import matplotlib.container as mcontainer

    bars = [c for c in ax.containers if isinstance(c, mcontainer.BarContainer)]
    if len(bars) != 1:
        return                              # 0 / grouped / stacked → leave alone
    patches = list(bars[0])
    n = len(patches)
    if n <= _MAX_PIE_SLICES:
        return
    heights = [float(p.get_height()) for p in patches]
    if len(set(round(h, 9) for h in heights)) <= 1:
        return                              # looks horizontal / degenerate → skip

    ticks = [t.get_text() for t in ax.get_xticklabels()]
    labels = ticks if sum(bool(t) for t in ticks) == n else [str(i + 1) for i in range(n)]
    labels = _resolve_category_names(df, labels)
    order = sorted(range(n), key=lambda i: heights[i], reverse=True)
    keep, rest = order[:_MAX_PIE_SLICES], order[_MAX_PIE_SLICES:]
    new_labels = [str(labels[i]) for i in keep] + ["Others"]
    new_heights = [heights[i] for i in keep] + [sum(heights[i] for i in rest)]

    title, xl, yl = ax.get_title(), ax.get_xlabel(), ax.get_ylabel()
    ax.cla()
    ax.bar(range(len(new_heights)), new_heights)
    ax.set_xticks(range(len(new_labels)))
    ax.set_xticklabels(new_labels, rotation=45, ha="right")
    ax.set_title(title)
    ax.set_xlabel(xl)
    ax.set_ylabel(yl)


def _apply_chart_legends(fig, df=None):
    """Ensure every axes carries appropriate symbology (a legend).

    Order of preference per axes:
      1. Pie  → category→colour legend from the stashed wedges/labels.
      2. Already has a legend → leave it.
      3. Labelled artists (multi-series line/scatter/grouped bar) → show legend.
      4. Single-series bar → colour bars by category + category legend.
    Other single-series charts (one line, scatter, box) have no meaningful
    legend to synthesise here and rely on the model following the prompt rule.
    """
    for ax in fig.axes:
        sym = getattr(ax, "_pie_symbology", None)
        if sym:
            wedges, labels = sym
            labels = _resolve_category_names(df, labels)
            try:
                ax.legend(wedges, labels, loc="center left",
                          bbox_to_anchor=(1.0, 0.5), fontsize="small")
            except Exception:
                pass
            continue

        if ax.get_legend() is not None:
            continue

        try:
            handles, labels = ax.get_legend_handles_labels()
            if labels:
                ax.legend(loc="best", fontsize="small")
                continue
        except Exception:
            pass

        try:
            _legend_for_bars(ax, df)
        except Exception:
            pass


def _cap_axes_pie(original_pie):
    """Wrap matplotlib Axes.pie to (1) collapse a many-slice pie to top-N +
    Others and (2) add a color→label legend.

    Direct calls (`ax.pie(x, labels=...)`) are reduced here. Calls from pandas
    skip the reduction (pandas strict-zips its own labels against the returned
    wedges, so the pandas path is reduced earlier in _cap_pandas_pie) but still
    get the legend."""
    def wrapper(self, x, *args, **kwargs):
        caller = sys._getframe(1).f_globals.get("__name__", "")
        from_pandas = caller.startswith("pandas")

        if not from_pandas and not args:
            try:
                vals = np.asarray(list(x), dtype=float)
            except (TypeError, ValueError):
                vals = None
            if vals is not None and vals.size > _MAX_PIE_SLICES:
                order = np.argsort(vals)[::-1]
                keep, rest = order[:_MAX_PIE_SLICES], order[_MAX_PIE_SLICES:]
                x = list(vals[keep]) + [float(vals[rest].sum())]
                labels = kwargs.get("labels")
                if labels is not None and len(list(labels)) == vals.size:
                    labels = list(labels)
                    kwargs["labels"] = [str(labels[i]) for i in keep] + ["Others"]
                kwargs.pop("explode", None)   # wrong length after collapsing
                kwargs.pop("colors", None)

        result = original_pie(self, x, *args, **kwargs)
        _stash_pie_symbology(self, result, kwargs.get("labels"))
        return result

    return wrapper


def _cap_pandas_pie(original_call):
    """Wrap pandas' PlotAccessor.__call__ so a Series pie is collapsed to
    top-N + Others BEFORE pandas builds its labels (covers .plot(kind='pie')
    and .plot.pie())."""
    def wrapper(self, *args, **kwargs):
        kind = kwargs.get("kind") or (args[0] if args and isinstance(args[0], str) else None)
        parent = getattr(self, "_parent", None)
        if kind == "pie" and isinstance(parent, pd.Series):
            s = parent.dropna()
            if s.size > _MAX_PIE_SLICES:
                s = s.sort_values(ascending=False)
                others = pd.Series({"Others": float(s.iloc[_MAX_PIE_SLICES:].sum())})
                reduced = pd.concat([s.iloc[:_MAX_PIE_SLICES], others])
                return original_call(reduced.plot, *args, **kwargs)
        return original_call(self, *args, **kwargs)

    return wrapper


# ─────────────────────────────────────────────────────────────────────────────
# Tool implementations
# ─────────────────────────────────────────────────────────────────────────────
def _analyze_data(inputs: dict, df: pd.DataFrame) -> dict:
    query_type = inputs.get("query_type", "all")
    columns    = inputs.get("columns") or None
    target     = df[columns] if columns else df
    result: dict = {}

    if query_type in ("schema", "all"):
        result["schema"] = {
            "shape": {"rows": len(df), "columns": len(df.columns)},
            "columns": {col: str(dt) for col, dt in df.dtypes.items()},
            "column_list": list(df.columns),
        }
    if query_type in ("statistics", "all"):
        num = target.select_dtypes(include=[np.number])
        if not num.empty:
            result["statistics"] = json.loads(num.describe().to_json())
    if query_type in ("sample", "all"):
        result["sample"] = json.loads(
            target.head(5).to_json(orient="records", date_format="iso")
        )
    if query_type in ("null_check", "all"):
        result["null_values"] = {
            col: {"count": int(df[col].isnull().sum()),
                  "percentage": round(df[col].isnull().mean() * 100, 2)}
            for col in df.columns
        }
    if query_type == "unique_values":
        cols = columns if columns else list(df.columns)
        result["unique_values"] = {
            col: {"count": int(df[col].nunique()),
                  "samples": df[col].dropna().unique()[:10].tolist()}
            for col in cols
        }
    return result


def _run_pandas_query(inputs: dict, df: pd.DataFrame) -> dict:
    code = inputs.get("code", "")
    ns   = _safe_exec(code, df)
    res  = ns.get("result")

    if isinstance(res, pd.DataFrame):
        return {
            "type": "dataframe",
            "data": json.loads(res.to_json(orient="records", date_format="iso")),
            "shape": {"rows": len(res), "columns": len(res.columns)},
            "columns": list(res.columns),
        }
    if isinstance(res, pd.Series):
        return {"type": "series", "data": json.loads(res.to_json()), "name": res.name}
    if res is not None:
        value = float(res) if isinstance(res, (np.integer, np.floating)) else str(res)
        return {"type": "scalar", "value": value}

    return {"type": "none", "message": "Assign your answer to 'result'."}


def _create_visualization(inputs: dict, df: pd.DataFrame, charts_dir: Path) -> dict:
    code  = inputs.get("code", "")
    title = inputs.get("title", "Chart")

    plt.close("all")

    # Cap pie charts at top-N + "Others" no matter how the model drew them
    # (direct ax.pie OR pandas .plot.pie). Both patches are restored afterwards.
    import matplotlib.axes
    _orig_axes_pie = matplotlib.axes.Axes.pie
    matplotlib.axes.Axes.pie = _cap_axes_pie(_orig_axes_pie)
    try:
        _PlotAccessor = type(pd.Series(dtype="float64").plot)
        _orig_pd_call = _PlotAccessor.__call__
        _PlotAccessor.__call__ = _cap_pandas_pie(_orig_pd_call)
    except Exception:
        _PlotAccessor = None
    try:
        ns = _safe_exec(code, df)
    finally:
        matplotlib.axes.Axes.pie = _orig_axes_pie
        if _PlotAccessor is not None:
            _PlotAccessor.__call__ = _orig_pd_call

    # Prefer the 'fig' variable from the exec namespace; fall back to gcf()
    fig = ns.get("fig")
    if fig is None:
        fignums = plt.get_fignums()
        if not fignums:
            return {"error": "No figure was created. Make sure the code calls plt.subplots() or a seaborn/pandas plot."}
        fig = plt.gcf()

    # Cap high-cardinality single-series bar charts (top-N + Others), then
    # ensure every chart carries appropriate symbology.
    for ax in fig.axes:
        try:
            _cap_bar_axes(ax, df)
        except Exception:
            pass
    _apply_chart_legends(fig, df)

    filename = f"chart_{uuid.uuid4().hex[:8]}.png"
    out_path = charts_dir / filename
    fig.savefig(str(out_path), dpi=150, bbox_inches="tight", facecolor="white")
    plt.close("all")

    return {"type": "chart", "filename": filename, "title": title,
            "message": f"Chart '{title}' created."}


def _create_derived_table(inputs: dict, df: pd.DataFrame) -> dict:
    code       = inputs.get("code", "")
    table_name = inputs.get("table_name", "Derived Table")
    ns         = _safe_exec(code, df)
    result_df  = ns.get("result_df")

    if result_df is None:
        return {"error": "Assign your derived DataFrame to 'result_df'."}
    if not isinstance(result_df, pd.DataFrame):
        result_df = pd.DataFrame(result_df)

    return {
        "type": "table",
        "table_name": table_name,
        "data": json.loads(result_df.to_json(orient="records", date_format="iso")),
        "columns": list(result_df.columns),
        "shape": {"rows": len(result_df), "columns": len(result_df.columns)},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Dispatcher
# ─────────────────────────────────────────────────────────────────────────────
def execute_tool(
    name: str,
    inputs: dict,
    df: pd.DataFrame,
    charts_dir: Path,
    rag=None,
    active_file: str | None = None,
) -> dict:
    try:
        if name == "analyze_data":
            return _analyze_data(inputs, df)
        if name == "run_pandas_query":
            return _run_pandas_query(inputs, df)
        if name == "create_visualization":
            return _create_visualization(inputs, df, charts_dir)
        if name == "create_derived_table":
            return _create_derived_table(inputs, df)
        if name == "search_context":
            if rag and active_file:
                return {"context": rag.search(inputs.get("query", ""), active_file)}
            return {"context": []}
        return {"error": f"Unknown tool: {name}"}
    except Exception as exc:
        return {"error": str(exc), "traceback": traceback.format_exc()}
