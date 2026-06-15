---
type: knowledge
customer: bcp
topic: model-evaluation
source: internal
tags: [knowledge, bcp, foundation-models, evaluation, reliability, mlflow, agent-evaluation, notebook, python, comments-generator]
created: 2026-06-03
updated: 2026-06-03
---

# Notebook — Harness de evaluación de modelos para el generador de comentarios

Notebook de referencia para comparar modelos fundacionales (LLM) candidatos sobre el generador de comentarios de Unity Catalog. Acompaña a la nota de campo *"Cómo medir la fiabilidad de un modelo fundacional"*.

**Cómo usarlo:** cada bloque `python` es una celda de un notebook de Databricks. Copiar en un notebook nuevo y adaptar nombres de catálogo/esquema/tabla y la lista de modelos. Es una plantilla — ajustar a las convenciones del banco.

**Requisitos:** Runtime con MLflow 3 (`mlflow>=3.0`), `databricks-agents`, acceso a Foundation Model API / AI Gateway y al esquema `resultados` del generador.

---

## 1. Configuración

```python
# Modelos candidatos a comparar. Se invocan por nombre vía Foundation Model API / AI Gateway.
# Cambiar de modelo es solo configuración: el resto del harness no cambia.
CANDIDATE_MODELS = [
    "databricks-claude-sonnet-4-5",
    "databricks-meta-llama-3-3-70b-instruct",
    "databricks-gpt-oss-120b",
    # ...agregar los que el banco quiera evaluar
]

# Juez NEUTRAL y FIJO: no debe ser ninguno de los candidatos (evita auto-favoritismo).
JUDGE_MODEL = "databricks-claude-opus-4-1"

# Configuración de generación fija para todos los candidatos (aísla la variable "modelo").
GEN_TEMPERATURE = 0.0
MAX_COMMENT_CHARS = 4000
N_CONSISTENCY_RUNS = 3        # corridas por input para medir varianza

# Ubicaciones
GOLDEN_TABLE = "main.gobierno_datos.golden_set_comentarios"   # set de prueba curado
EXPERIMENT_PATH = "/Shared/eval-comentarios-fundacionales"
```

---

## 2. El golden set

El set de prueba curado. Una fila por objeto (schema/tabla/columna) a comentar, con su contexto y, cuando exista, el comentario "gold" aprobado por un experto.

```python
from pyspark.sql import functions as F

# Estructura esperada del golden set:
#   object_id        : identificador único del objeto (ej. catalogo.esquema.tabla.columna)
#   object_level     : 'schema' | 'table' | 'column'
#   context_text     : contexto provisto al generador (insumos + metadata) — máx 30K chars
#   gold_comment     : comentario de referencia aprobado por experto (puede ser NULL)
#   domain           : dominio de negocio (riesgos, finanzas, clientes, ...) — para estratificar
#   is_sensitive     : boolean — para verificar fuga de PII
golden = spark.table(GOLDEN_TABLE)

# Validar cobertura/estratificación antes de correr (que el set sea representativo).
display(golden.groupBy("domain", "object_level").count().orderBy("domain"))
print("Total objetos:", golden.count(),
      "| con comentario gold:", golden.filter(F.col("gold_comment").isNotNull()).count())
```

---

## 3. Generación: invocar cada modelo candidato

Función única de generación. El modelo es un parámetro; el prompt y el contexto se mantienen fijos.

```python
from mlflow.deployments import get_deploy_client
import time

client = get_deploy_client("databricks")

PROMPT_TEMPLATE = """Eres un experto en gobierno de datos de un banco.
Genera un comentario de negocio en ESPAÑOL para el siguiente objeto de Unity Catalog.
Usa ÚNICAMENTE la información del contexto. No inventes significados ni reglas.
Máximo {max_chars} caracteres. No incluyas datos personales (PII).

Nivel del objeto: {level}
Contexto:
{context}

Comentario:"""

def generate_comment(model: str, level: str, context: str) -> dict:
    prompt = PROMPT_TEMPLATE.format(max_chars=MAX_COMMENT_CHARS, level=level, context=context)
    t0 = time.time()
    resp = client.predict(
        endpoint=model,
        inputs={"messages": [{"role": "user", "content": prompt}],
                "temperature": GEN_TEMPERATURE, "max_tokens": 1024},
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
```

---

## 4. Scorers determinísticos (baratos, objetivos)

Se corren primero porque no cuestan tokens: idioma, longitud, PII, comentario no vacío.

```python
import re

PII_PATTERNS = [
    r"\b\d{8,11}\b",                       # DNI / RUC / números largos
    r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b",      # emails
    r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b",  # tarjetas
]

def deterministic_scores(comment: str, is_sensitive: bool) -> dict:
    non_empty = len(comment.strip()) > 0
    within_len = len(comment) <= MAX_COMMENT_CHARS
    has_pii = any(re.search(p, comment) for p in PII_PATTERNS)
    # Heurística simple de idioma español (validar con detector si se requiere precisión).
    is_spanish = bool(re.search(r"\b(de|la|el|los|las|que|para|con)\b", comment.lower()))
    return {
        "det_non_empty": int(non_empty),
        "det_within_len": int(within_len),
        "det_no_pii": int(not has_pii),
        "det_is_spanish": int(is_spanish),
    }
```

---

## 5. Scorers con juez LLM (reusa los criterios del auditor existente)

Formaliza los 5 criterios de `04_audit_comments.py` como jueces de Mosaic AI Agent Evaluation. El juez evalúa contra el contexto fuente (no requiere comentario de referencia).

```python
import json

JUDGE_PROMPT = """Eres un auditor experto de comentarios de negocio de Unity Catalog.
Evalúa el COMENTARIO contra el CONTEXTO. Devuelve SOLO un JSON con 5 claves (1=cumple, 0=falla):
- grounding: 1 si el comentario NO afirma nada fuera del contexto (sin alucinación)
- terminologia: 1 si usa términos canónicos correctos
- granularidad: 1 si el alcance corresponde al nivel del objeto
- completitud: 1 si no omite información clave del contexto
- idioma_estilo: 1 si está en español claro y bien formado

CONTEXTO:
{context}

COMENTARIO:
{comment}

JSON:"""

def judge_scores(level: str, context: str, comment: str) -> dict:
    prompt = JUDGE_PROMPT.format(context=context[:30000], comment=comment)
    resp = client.predict(
        endpoint=JUDGE_MODEL,
        inputs={"messages": [{"role": "user", "content": prompt}], "temperature": 0.0},
    )
    raw = resp["choices"][0]["message"]["content"]
    try:
        scores = json.loads(re.search(r"\{.*\}", raw, re.DOTALL).group(0))
    except Exception:
        scores = {k: 0 for k in ["grounding", "terminologia", "granularidad",
                                 "completitud", "idioma_estilo"]}
    return {f"judge_{k}": int(v) for k, v in scores.items()}
```

> **Calibración:** antes de confiar en el juez a escala, exportar una muestra (~30 ítems) para revisión humana y medir el acuerdo juez ↔ experto. Si el acuerdo es bajo, ajustar el `JUDGE_PROMPT`.

---

## 6. Consistencia: varianza entre corridas

Un modelo fiable es estable. Se genera N veces el mismo input y se mide cuánto varía el resultado.

```python
from difflib import SequenceMatcher
from statistics import mean

def consistency_score(model: str, level: str, context: str, n: int = N_CONSISTENCY_RUNS) -> float:
    outs = [generate_comment(model, level, context)["comment"] for _ in range(n)]
    sims = [SequenceMatcher(None, outs[0], o).ratio() for o in outs[1:]]
    return mean(sims) if sims else 1.0   # 1.0 = idéntico entre corridas
```

---

## 7. Ejecutar la evaluación y registrar en MLflow

Cada modelo candidato = un run de MLflow sobre el mismo golden set. Quedan comparables en el Evaluation UI.

```python
import mlflow
from statistics import mean

mlflow.set_experiment(EXPERIMENT_PATH)
rows = golden.toPandas().to_dict("records")

results = {}
for model in CANDIDATE_MODELS:
    with mlflow.start_run(run_name=model):
        per_item = []
        for r in rows:
            gen = generate_comment(model, r["object_level"], r["context_text"])
            det = deterministic_scores(gen["comment"], r.get("is_sensitive", False))
            jud = judge_scores(r["object_level"], r["context_text"], gen["comment"])
            cons = consistency_score(model, r["object_level"], r["context_text"])
            per_item.append({**det, **jud, "consistency": cons,
                             "latency_ms": gen["latency_ms"],
                             "tokens_in": gen["tokens_in"], "tokens_out": gen["tokens_out"]})

        agg = {k: mean(x[k] for x in per_item) for k in per_item[0]}
        mlflow.log_params({"model": model, "temperature": GEN_TEMPERATURE,
                           "judge": JUDGE_MODEL, "n_items": len(rows)})
        mlflow.log_metrics(agg)
        results[model] = agg
        print(f"{model}: {agg}")
```

---

## 8. Latencia y costo por modelo

```python
# Precio por 1M tokens — completar con la tarifa vigente de cada endpoint (pay-go o provisioned).
PRICE_PER_1M = {
    "databricks-claude-sonnet-4-5": {"in": 3.0, "out": 15.0},
    # ...completar por modelo
}

def cost_per_1k_comments(model, agg):
    p = PRICE_PER_1M.get(model, {"in": 0, "out": 0})
    cost_one = (agg["tokens_in"] / 1e6) * p["in"] + (agg["tokens_out"] / 1e6) * p["out"]
    return cost_one * 1000

for m, agg in results.items():
    print(f"{m}: ${cost_per_1k_comments(m, agg):.2f} / 1K comentarios | "
          f"latencia media {agg['latency_ms']:.0f} ms")
```

---

## 9. Scorecard ponderado de decisión

Combina calidad + consistencia + costo + latencia. **Los pesos los define el banco.**

```python
import pandas as pd

WEIGHTS = {                 # deben sumar 1.0
    "grounding": 0.35,
    "completitud_terminologia": 0.25,
    "consistency": 0.15,
    "cost": 0.15,
    "latency": 0.10,
}

def normalize(values, lower_is_better=False):
    lo, hi = min(values), max(values)
    if hi == lo:
        return {k: 1.0 for k in values}
    return {i: (1 - (v - lo) / (hi - lo)) if lower_is_better else (v - lo) / (hi - lo)
            for i, v in enumerate(values)}

models = list(results.keys())
costs = [cost_per_1k_comments(m, results[m]) for m in models]
lats = [results[m]["latency_ms"] for m in models]
cost_n = normalize(costs, lower_is_better=True)
lat_n = normalize(lats, lower_is_better=True)

scorecard = []
for i, m in enumerate(models):
    a = results[m]
    score = (
        WEIGHTS["grounding"] * a["judge_grounding"] +
        WEIGHTS["completitud_terminologia"] * mean([a["judge_completitud"], a["judge_terminologia"]]) +
        WEIGHTS["consistency"] * a["consistency"] +
        WEIGHTS["cost"] * cost_n[i] +
        WEIGHTS["latency"] * lat_n[i]
    )
    scorecard.append({"modelo": m, "score_final": round(score, 3),
                      "grounding": round(a["judge_grounding"], 2),
                      "consistencia": round(a["consistency"], 2),
                      "costo_1k": round(costs[i], 2),
                      "latencia_ms": round(a["latency_ms"], 0)})

df = pd.DataFrame(scorecard).sort_values("score_final", ascending=False)
display(spark.createDataFrame(df))
```

---

## 10. Comparación lado a lado en MLflow

Abrir el experimento `EXPERIMENT_PATH` en el **Evaluation UI** de MLflow para comparar runs por métrica, inspeccionar comentarios individuales y trazar fallas de criterio. El run con mayor `score_final` es el candidato recomendado, sujeto a la validación humana de la muestra calibrada.

---

## Notas operativas

- **Tag `RemoveAfter`:** si se despliegan recursos con costo (SQL warehouse, serving endpoints, instancias) para correr el harness, agregar el tag `RemoveAfter` con fecha de hoy + 6 meses.
- **Reuso del auditor existente:** el `JUDGE_PROMPT` replica los 5 criterios de `04_audit_comments.py`. Si el banco ajusta ese auditor, reflejar el cambio aquí para mantener consistencia.
- **Fase 2 (producción):** tras elegir el modelo, los mismos scorers se reusan como jueces online sobre tablas de inferencia + Lakehouse Monitoring para detectar *drift*.
