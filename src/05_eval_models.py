# Databricks notebook source
# MAGIC %md
# MAGIC # Evaluación de Modelos Fundacionales v7
# MAGIC ## Bake-off offline para elegir el modelo del generador de comentarios
# MAGIC
# MAGIC Compara varios **modelos fundacionales candidatos** sobre la tarea
# MAGIC concreta del generador de comentarios de Unity Catalog y produce un
# MAGIC **scorecard comparable**. No genera ni aplica comentarios al catálogo:
# MAGIC es un proceso de **selección offline**.
# MAGIC
# MAGIC Acompaña a la nota de campo
# MAGIC *"Cómo medir la fiabilidad de un modelo fundacional"*
# MAGIC (`nota-fiabilidad-modelos-fundacionales.md`).
# MAGIC
# MAGIC ### Principio rector
# MAGIC
# MAGIC Todos los candidatos corren sobre el **mismo golden set fijo**, con el
# MAGIC **mismo prompt, contexto y temperatura**. Solo cambia el modelo. Así el
# MAGIC resultado aísla la variable "modelo" y la decisión es defendible.
# MAGIC
# MAGIC ### Cómo se mide la fiabilidad
# MAGIC
# MAGIC - **Scorers determinísticos** (gratis): idioma, longitud, PII, no-vacío.
# MAGIC - **Juez LLM neutral**: reutiliza los criterios de `src/audit_criteria.py`
# MAGIC   (los mismos que aplica `04_audit_comments.py`). El juez **no** debe ser
# MAGIC   ninguno de los candidatos, para evitar auto-favoritismo.
# MAGIC - **Consistencia**: varianza entre N corridas del mismo input.
# MAGIC - **Latencia y costo** por modelo.
# MAGIC - **Scorecard ponderado** (los pesos los define la organización).
# MAGIC
# MAGIC Cada modelo candidato se registra como un **run de MLflow** sobre el
# MAGIC mismo experimento, comparables en el Evaluation UI.
# MAGIC
# MAGIC ### Golden set
# MAGIC
# MAGIC Set de prueba curado (ver DDL en `01_setup_schema.py`,
# MAGIC tabla `golden_set_comentarios`). Una fila por objeto a comentar con su
# MAGIC contexto y, cuando exista, el comentario "gold" aprobado por un experto.

# COMMAND ----------

# MAGIC %pip install mlflow -q
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Lectura y validación de parámetros

# COMMAND ----------

dbutils.widgets.text(
    "candidate_models", "",
    "Modelos candidatos (separados por coma)",
)
dbutils.widgets.text(
    "judge_model", "databricks-claude-opus-4-1",
    "Modelo juez (neutral, no candidato)",
)
dbutils.widgets.text("results_catalog", "", "Catálogo de resultados")
dbutils.widgets.text("results_schema", "", "Esquema de resultados")
dbutils.widgets.text(
    "golden_table", "",
    "Tabla golden set (vacío = <results>.golden_set_comentarios)",
)
dbutils.widgets.text(
    "experiment_path", "/Shared/eval-comentarios-fundacionales",
    "Experimento MLflow",
)
dbutils.widgets.text(
    "gen_temperature", "0.0", "Temperatura de generación",
)
dbutils.widgets.text(
    "n_consistency_runs", "3", "Corridas por input (consistencia)",
)
dbutils.widgets.text(
    "max_comment_chars", "500", "Máximo de caracteres por comentario",
)

RESULTS_CATALOG = dbutils.widgets.get("results_catalog").strip()
RESULTS_SCHEMA = dbutils.widgets.get("results_schema").strip()
JUDGE_MODEL = dbutils.widgets.get("judge_model").strip()
GOLDEN_TABLE = dbutils.widgets.get("golden_table").strip()
EXPERIMENT_PATH = dbutils.widgets.get("experiment_path").strip()

CANDIDATE_MODELS = [
    m.strip()
    for m in dbutils.widgets.get("candidate_models").split(",")
    if m.strip()
]

_required = {
    "candidate_models": CANDIDATE_MODELS,
    "judge_model": JUDGE_MODEL,
    "results_catalog": RESULTS_CATALOG,
    "results_schema": RESULTS_SCHEMA,
    "experiment_path": EXPERIMENT_PATH,
}
_missing = [k for k, v in _required.items() if not v]
if _missing:
    raise ValueError(
        f"Parámetros obligatorios sin valor: {', '.join(_missing)}"
    )

# El juez debe ser neutral: no puede ser uno de los candidatos.
if JUDGE_MODEL in CANDIDATE_MODELS:
    raise ValueError(
        f"El juez '{JUDGE_MODEL}' no puede estar en la lista de candidatos "
        "(evita auto-favoritismo). Usa un modelo distinto como juez."
    )

# Golden set: si no se especifica, se asume en el esquema de resultados.
if not GOLDEN_TABLE:
    GOLDEN_TABLE = (
        f"{RESULTS_CATALOG}.{RESULTS_SCHEMA}.golden_set_comentarios"
    )
if GOLDEN_TABLE.count(".") != 2:
    raise ValueError(
        "'golden_table' debe tener formato 'catalogo.esquema.tabla'."
    )

try:
    GEN_TEMPERATURE = float(dbutils.widgets.get("gen_temperature").strip())
    if not 0.0 <= GEN_TEMPERATURE <= 2.0:
        raise ValueError
except ValueError:
    raise ValueError("'gen_temperature' debe ser un número entre 0.0 y 2.0.")

try:
    N_CONSISTENCY_RUNS = int(dbutils.widgets.get("n_consistency_runs").strip())
    if N_CONSISTENCY_RUNS < 1:
        raise ValueError
except ValueError:
    raise ValueError("'n_consistency_runs' debe ser un entero >= 1.")

try:
    MAX_COMMENT_CHARS = int(dbutils.widgets.get("max_comment_chars").strip())
    if MAX_COMMENT_CHARS < 1:
        raise ValueError
except ValueError:
    raise ValueError("'max_comment_chars' debe ser un entero >= 1.")

print("=" * 60)
print("PARÁMETROS DE EVALUACIÓN")
print("=" * 60)
print(f"  Candidatos        : {', '.join(CANDIDATE_MODELS)}")
print(f"  Juez (neutral)    : {JUDGE_MODEL}")
print(f"  Golden set        : {GOLDEN_TABLE}")
print(f"  Experimento       : {EXPERIMENT_PATH}")
print(f"  Temperatura       : {GEN_TEMPERATURE}")
print(f"  Corridas consist. : {N_CONSISTENCY_RUNS}")
print(f"  Máx. caracteres   : {MAX_COMMENT_CHARS}")
print("=" * 60)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Configuración del entorno

# COMMAND ----------

import json
import logging
import os
import re
import sys
import time
from difflib import SequenceMatcher
from statistics import mean

import pandas as pd
from mlflow.deployments import get_deploy_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("comments_eval_v7")

_GOLDEN_TABLE_SQL = ".".join(f"`{p}`" for p in GOLDEN_TABLE.split("."))

_notebook_path = (
    dbutils.notebook.entry_point.getDbutils()
    .notebook()
    .getContext()
    .notebookPath()
    .get()
)
PROJECT_ROOT = os.path.dirname(os.path.dirname(_notebook_path))
SRC_DIR = f"/Workspace{PROJECT_ROOT}/src"

if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

# Reutiliza los criterios canónicos del auditor (fuente única de verdad).
from audit_criteria import format_for_prompt, get_criteria_ids

CRITERIA_IDS = get_criteria_ids()
CRITERIA_BLOCK = format_for_prompt()

deploy_client = get_deploy_client("databricks")

logger.info(f"Candidatos : {CANDIDATE_MODELS}")
logger.info(f"Juez       : {JUDGE_MODEL}")
logger.info(f"Golden set : {GOLDEN_TABLE}")
logger.info(f"Criterios juez (de audit_criteria.py): {CRITERIA_IDS}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. El golden set
# MAGIC
# MAGIC Una fila por objeto (schema/tabla/columna) a comentar, con su contexto
# MAGIC y, cuando exista, el comentario "gold" aprobado por un experto. Antes de
# MAGIC correr se valida cobertura/estratificación (que el set sea
# MAGIC representativo del estate real).

# COMMAND ----------

from pyspark.sql import functions as F

golden = spark.table(GOLDEN_TABLE)

_expected_cols = {
    "object_id", "object_level", "context_text",
    "gold_comment", "domain", "is_sensitive",
}
_actual_cols = set(golden.columns)
_missing_cols = _expected_cols - _actual_cols
if _missing_cols:
    raise ValueError(
        f"El golden set '{GOLDEN_TABLE}' no tiene las columnas esperadas. "
        f"Faltan: {sorted(_missing_cols)}. Ver DDL en 01_setup_schema.py."
    )

total_golden = golden.count()
if total_golden == 0:
    raise ValueError(
        f"El golden set '{GOLDEN_TABLE}' está vacío. Cúralo antes de evaluar."
    )

print("Estratificación del golden set:")
display(
    golden.groupBy("domain", "object_level").count().orderBy("domain")
)
print(
    f"Total objetos: {total_golden}",
    "| con comentario gold:",
    golden.filter(F.col("gold_comment").isNotNull()).count(),
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Generación: invocar cada modelo candidato
# MAGIC
# MAGIC Función única de generación. El modelo es un parámetro; el prompt y el
# MAGIC contexto se mantienen fijos. La persona y las reglas espejan las de
# MAGIC `02_generate_comments.py` para que el bake-off sea representativo del
# MAGIC trabajo real del generador.

# COMMAND ----------

# Límite de caracteres por nivel, alineado con el generador real
# (esquema/tabla = 500, columna = 300). Se acota por MAX_COMMENT_CHARS.
_LEVEL_MAX_CHARS = {
    "schema": min(500, MAX_COMMENT_CHARS),
    "table": min(500, MAX_COMMENT_CHARS),
    "column": min(300, MAX_COMMENT_CHARS),
}

_LEVEL_LABEL = {
    "schema": "esquema",
    "table": "tabla",
    "column": "columna",
}


def _build_prompt(level: str, context: str) -> str:
    max_chars = _LEVEL_MAX_CHARS.get(level, MAX_COMMENT_CHARS)
    nivel_es = _LEVEL_LABEL.get(level, level)
    return (
        "Eres un experto en documentación de datos orientada a usuarios "
        "de negocio de una organización.\n"
        f"{context}\n"
        f"\nGenera una definición de negocio para el siguiente objeto de "
        f"tipo {nivel_es}.\n\n"
        "La definición debe:\n"
        "- Utilizar solo la información dada en el contexto, no "
        "conocimiento previo.\n"
        "- Estar en español, lenguaje claro para usuarios de negocio.\n"
        f"- Tener máximo {max_chars} caracteres.\n"
        "- No incluir datos personales (PII).\n"
        "- Responde ÚNICAMENTE con la definición, sin comillas."
    )


def generate_comment(model: str, level: str, context: str) -> dict:
    """Invoca un modelo candidato y mide latencia + tokens."""
    prompt = _build_prompt(level, context)
    t0 = time.time()
    resp = deploy_client.predict(
        endpoint=model,
        inputs={
            "messages": [{"role": "user", "content": prompt}],
            "temperature": GEN_TEMPERATURE,
            "max_tokens": 1024,
        },
    )
    latency_ms = (time.time() - t0) * 1000
    choice = resp["choices"][0]
    usage = resp.get("usage", {})
    return {
        "comment": choice["message"]["content"].strip(),
        "latency_ms": latency_ms,
        "tokens_in": usage.get("prompt_tokens", 0),
        "tokens_out": usage.get("completion_tokens", 0),
    }


# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Scorers determinísticos (baratos, objetivos)
# MAGIC
# MAGIC Se corren primero porque no cuestan tokens: idioma, longitud, PII,
# MAGIC comentario no vacío.

# COMMAND ----------

PII_PATTERNS = [
    r"\b\d{8,11}\b",                              # DNI / RUC / números largos
    r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b",              # emails
    r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b",   # tarjetas
]


def deterministic_scores(comment: str) -> dict:
    non_empty = len(comment.strip()) > 0
    within_len = len(comment) <= MAX_COMMENT_CHARS
    has_pii = any(re.search(p, comment) for p in PII_PATTERNS)
    # Heurística simple de idioma español (validar con detector si se
    # requiere mayor precisión).
    is_spanish = bool(
        re.search(r"\b(de|la|el|los|las|que|para|con)\b", comment.lower())
    )
    return {
        "det_non_empty": int(non_empty),
        "det_within_len": int(within_len),
        "det_no_pii": int(not has_pii),
        "det_is_spanish": int(is_spanish),
    }


# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Juez LLM (reutiliza los criterios del auditor)
# MAGIC
# MAGIC El juez evalúa el comentario contra el contexto fuente usando los
# MAGIC mismos criterios de `src/audit_criteria.py`. Devuelve 1=cumple / 0=falla
# MAGIC por criterio. No requiere comentario de referencia.

# COMMAND ----------

# Claves del JSON que devuelve el juez: una por criterio, en minúsculas.
_JUDGE_KEYS = [c.lower() for c in CRITERIA_IDS]

JUDGE_PROMPT_TEMPLATE = (
    "Eres un auditor experto de comentarios de negocio de Unity Catalog. "
    "Evalúa el COMENTARIO contra el CONTEXTO usando los criterios de abajo. "
    "Usa únicamente la información del contexto; no apliques conocimiento "
    "previo.\n\n"
    "=== CRITERIOS (el comentario CUMPLE un criterio cuando NO incurre en "
    "la falla descrita) ===\n"
    f"{CRITERIA_BLOCK}\n\n"
    "=== CONTEXTO ===\n{context}\n\n"
    "=== OBJETO ===\nNivel: {level}\n\n"
    "=== COMENTARIO ===\n{comment}\n\n"
    "=== RESPUESTA ===\n"
    "Responde ÚNICAMENTE con un JSON válido, sin markdown ni texto extra, "
    "con UNA clave por criterio (en minúsculas) y valor 1 (cumple) o "
    "0 (falla). Claves exactas: " + ", ".join(_JUDGE_KEYS) + ".\n"
    "Ejemplo de forma: {{" + ", ".join(f'"{k}": 1' for k in _JUDGE_KEYS) + "}}"
)

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def judge_scores(level: str, context: str, comment: str) -> dict:
    prompt = JUDGE_PROMPT_TEMPLATE.format(
        context=context[:30000], level=level, comment=comment
    )
    try:
        resp = deploy_client.predict(
            endpoint=JUDGE_MODEL,
            inputs={
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.0,
                "max_tokens": 300,
            },
        )
        raw = resp["choices"][0]["message"]["content"]
        match = _JSON_BLOCK_RE.search(raw)
        scores = json.loads(match.group(0)) if match else {}
    except Exception as exc:
        logger.warning(f"    ⚠ Juez no parseable / error: {str(exc)[:120]}")
        scores = {}
    # Conserva una clave por criterio; ausente o no-1 => 0 (conservador).
    return {
        f"judge_{k}": int(scores.get(k, 0) == 1) for k in _JUDGE_KEYS
    }


# COMMAND ----------

# MAGIC %md
# MAGIC ### Calibración (recomendado antes de confiar en el juez a escala)
# MAGIC
# MAGIC Exportar una muestra (~30 ítems) para revisión humana y medir el
# MAGIC acuerdo juez ↔ experto. Si el acuerdo es bajo, ajustar los criterios en
# MAGIC `src/audit_criteria.py` (se reflejará automáticamente aquí).

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Consistencia: varianza entre corridas
# MAGIC
# MAGIC Un modelo fiable es estable. Se genera N veces el mismo input y se mide
# MAGIC cuánto varía el resultado (1.0 = idéntico entre corridas).

# COMMAND ----------

def consistency_score(model: str, level: str, context: str) -> float:
    outs = [
        generate_comment(model, level, context)["comment"]
        for _ in range(N_CONSISTENCY_RUNS)
    ]
    sims = [SequenceMatcher(None, outs[0], o).ratio() for o in outs[1:]]
    return mean(sims) if sims else 1.0


# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. Ejecutar la evaluación y registrar en MLflow
# MAGIC
# MAGIC Cada modelo candidato = un run de MLflow sobre el mismo golden set.
# MAGIC Quedan comparables en el Evaluation UI.

# COMMAND ----------

import mlflow

mlflow.set_experiment(EXPERIMENT_PATH)
rows = golden.toPandas().to_dict("records")

results = {}
for model in CANDIDATE_MODELS:
    logger.info(f"Evaluando modelo: {model} sobre {len(rows)} objetos")
    with mlflow.start_run(run_name=model):
        per_item = []
        for r in rows:
            level = r["object_level"]
            context = r["context_text"] or ""
            try:
                gen = generate_comment(model, level, context)
            except Exception as exc:
                logger.error(
                    f"    ✗ Generación falló ({r['object_id']}): "
                    f"{str(exc)[:160]}"
                )
                continue
            det = deterministic_scores(gen["comment"])
            jud = judge_scores(level, context, gen["comment"])
            cons = consistency_score(model, level, context)
            per_item.append({
                **det, **jud, "consistency": cons,
                "latency_ms": gen["latency_ms"],
                "tokens_in": gen["tokens_in"],
                "tokens_out": gen["tokens_out"],
            })

        if not per_item:
            logger.warning(f"  Sin ítems evaluados para {model}; se omite.")
            continue

        agg = {k: mean(x[k] for x in per_item) for k in per_item[0]}
        mlflow.log_params({
            "model": model,
            "temperature": GEN_TEMPERATURE,
            "judge": JUDGE_MODEL,
            "n_items": len(per_item),
            "n_consistency_runs": N_CONSISTENCY_RUNS,
        })
        mlflow.log_metrics(agg)
        results[model] = agg
        logger.info(f"  {model}: {json.dumps(agg, ensure_ascii=False)}")

if not results:
    raise RuntimeError("Ningún modelo produjo resultados evaluables.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 9. Latencia y costo por modelo
# MAGIC
# MAGIC Completar `PRICE_PER_1M` con la tarifa vigente de cada endpoint
# MAGIC (pay-go o provisioned). Sin tarifa, el costo se reporta como 0.

# COMMAND ----------

# Precio por 1M de tokens — COMPLETAR con la tarifa vigente por endpoint.
PRICE_PER_1M = {
    "databricks-claude-sonnet-4-5": {"in": 3.0, "out": 15.0},
    # ...completar por modelo candidato
}


def cost_per_1k_comments(model: str, agg: dict) -> float:
    p = PRICE_PER_1M.get(model, {"in": 0.0, "out": 0.0})
    cost_one = (
        (agg["tokens_in"] / 1e6) * p["in"]
        + (agg["tokens_out"] / 1e6) * p["out"]
    )
    return cost_one * 1000


for m, agg in results.items():
    if m not in PRICE_PER_1M:
        logger.warning(
            f"  ⚠ Sin tarifa para '{m}' en PRICE_PER_1M; costo = 0."
        )
    print(
        f"{m}: ${cost_per_1k_comments(m, agg):.2f} / 1K comentarios | "
        f"latencia media {agg['latency_ms']:.0f} ms"
    )

# COMMAND ----------

# MAGIC %md
# MAGIC ## 10. Scorecard ponderado de decisión
# MAGIC
# MAGIC Combina calidad + consistencia + costo + latencia. **Los pesos los
# MAGIC define la organización** (deben sumar 1.0). Las dimensiones de calidad
# MAGIC se derivan de los criterios cargados desde `audit_criteria.py`.

# COMMAND ----------

# Pesos del scorecard — ajustar según prioridades de la organización.
WEIGHTS = {
    "calidad": 0.60,       # promedio de los criterios del juez
    "consistency": 0.15,
    "cost": 0.15,
    "latency": 0.10,
}
assert abs(sum(WEIGHTS.values()) - 1.0) < 1e-9, "Los pesos deben sumar 1.0"


def normalize(values: list, lower_is_better: bool = False) -> dict:
    lo, hi = min(values), max(values)
    if hi == lo:
        return {i: 1.0 for i in range(len(values))}
    return {
        i: (1 - (v - lo) / (hi - lo)) if lower_is_better
        else (v - lo) / (hi - lo)
        for i, v in enumerate(values)
    }


models = list(results.keys())
costs = [cost_per_1k_comments(m, results[m]) for m in models]
lats = [results[m]["latency_ms"] for m in models]
cost_n = normalize(costs, lower_is_better=True)
lat_n = normalize(lats, lower_is_better=True)

_judge_metric_keys = [f"judge_{k}" for k in _JUDGE_KEYS]

scorecard = []
for i, m in enumerate(models):
    a = results[m]
    calidad = mean(a[k] for k in _judge_metric_keys)
    score = (
        WEIGHTS["calidad"] * calidad
        + WEIGHTS["consistency"] * a["consistency"]
        + WEIGHTS["cost"] * cost_n[i]
        + WEIGHTS["latency"] * lat_n[i]
    )
    scorecard.append({
        "modelo": m,
        "score_final": round(score, 3),
        "calidad": round(calidad, 2),
        "consistencia": round(a["consistency"], 2),
        "costo_1k": round(costs[i], 2),
        "latencia_ms": round(a["latency_ms"], 0),
    })

scorecard_df = (
    pd.DataFrame(scorecard).sort_values("score_final", ascending=False)
)
display(spark.createDataFrame(scorecard_df))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 11. Resumen y candidato recomendado
# MAGIC
# MAGIC El run con mayor `score_final` es el candidato recomendado, **sujeto a
# MAGIC la validación humana** de la muestra calibrada. Abrir el experimento en
# MAGIC el Evaluation UI de MLflow para comparar runs lado a lado e inspeccionar
# MAGIC comentarios individuales.

# COMMAND ----------

best = scorecard_df.iloc[0]
print("=" * 60)
print("RESUMEN DE EVALUACIÓN")
print("=" * 60)
print(f"  Modelos evaluados : {len(models)}")
print(f"  Objetos golden    : {len(rows)}")
print(f"  Experimento       : {EXPERIMENT_PATH}")
print(f"  Candidato top     : {best['modelo']} "
      f"(score {best['score_final']})")
print("=" * 60)

dbutils.notebook.exit(
    f"Evaluados: {len(models)} | Top: {best['modelo']} "
    f"(score {best['score_final']})"
)
