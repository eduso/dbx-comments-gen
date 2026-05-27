# Databricks notebook source
# MAGIC %md
# MAGIC # Auditor de Comentarios v6
# MAGIC ## Validación independiente de comentarios generados
# MAGIC
# MAGIC Proceso independiente que evalúa si los comentarios persistidos en
# MAGIC `resultados` están apegados a los insumos provistos
# MAGIC (`input/mapping.md` + opcionalmente `input/audit_mapping.md`),
# MAGIC usando un modelo fundacional vía Foundation Model API.
# MAGIC
# MAGIC No genera ni aplica comentarios. Solo escribe el veredicto en las
# MAGIC columnas `criterio_fallido` y `detalles_criterio_fallido` de la
# MAGIC tabla `resultados`. **No modifica `status`.**
# MAGIC
# MAGIC ### Catálogo de criterios
# MAGIC
# MAGIC La lista de criterios se define en `src/audit_criteria.py`. Para
# MAGIC agregar/quitar/modificar un criterio basta editar ese archivo.
# MAGIC
# MAGIC ### Comportamiento idempotente
# MAGIC
# MAGIC Cada corrida sobrescribe el veredicto previo. Si el modelo dictamina
# MAGIC que la fila está OK, las columnas vuelven a NULL.

# COMMAND ----------

# MAGIC %pip install python-docx mlflow openpyxl -q
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Lectura y validación de parámetros

# COMMAND ----------

dbutils.widgets.text("results_catalog", "", "Catálogo de resultados")
dbutils.widgets.text("results_schema", "", "Esquema de resultados")
dbutils.widgets.text(
    "id_ejecucion", "", "ID de ejecución (vacío = todas las filas)"
)
dbutils.widgets.dropdown(
    "audit_only_approved", "yes", ["yes", "no"],
    "Auditar solo status='aprobado'",
)
dbutils.widgets.text(
    "audit_model_endpoint",
    "databricks-claude-sonnet-4-5",
    "Modelo fundacional para auditoría",
)

RESULTS_CATALOG = dbutils.widgets.get("results_catalog").strip()
RESULTS_SCHEMA = dbutils.widgets.get("results_schema").strip()
ID_EJECUCION = dbutils.widgets.get("id_ejecucion").strip()
AUDIT_ONLY_APPROVED = (
    dbutils.widgets.get("audit_only_approved").strip().lower() == "yes"
)
AUDIT_MODEL_ENDPOINT = dbutils.widgets.get("audit_model_endpoint").strip()

_required = {
    "results_catalog": RESULTS_CATALOG,
    "results_schema": RESULTS_SCHEMA,
    "audit_model_endpoint": AUDIT_MODEL_ENDPOINT,
}
_missing = [k for k, v in _required.items() if not v]
if _missing:
    raise ValueError(
        f"Parámetros obligatorios sin valor: {', '.join(_missing)}"
    )

print("=" * 60)
print("PARÁMETROS DE AUDITORÍA")
print("=" * 60)
print(f"  Resultados en       : {RESULTS_CATALOG}.{RESULTS_SCHEMA}")
print(f"  ID ejecución        : {ID_EJECUCION or '(todas)'}")
print(f"  Solo aprobados      : {AUDIT_ONLY_APPROVED}")
print(f"  Modelo auditoría    : {AUDIT_MODEL_ENDPOINT}")
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
from datetime import datetime, timezone

import pandas as pd
from mlflow.deployments import get_deploy_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("comments_auditor_v6")

RESULTS_TABLE = f"{RESULTS_CATALOG}.{RESULTS_SCHEMA}.resultados"
_RESULTS_TABLE_SQL = ".".join(f"`{p}`" for p in RESULTS_TABLE.split("."))

_notebook_path = (
    dbutils.notebook.entry_point.getDbutils()
    .notebook()
    .getContext()
    .notebookPath()
    .get()
)
PROJECT_ROOT = os.path.dirname(os.path.dirname(_notebook_path))
INPUT_DIR = f"/Workspace{PROJECT_ROOT}/input"
SRC_DIR = f"/Workspace{PROJECT_ROOT}/src"

if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from audit_criteria import AUDIT_CRITERIA, format_for_prompt, get_criteria_ids

VALID_CRITERIA = set(get_criteria_ids())

deploy_client = get_deploy_client("databricks")

logger.info(f"Endpoint auditoría: {AUDIT_MODEL_ENDPOINT}")
logger.info(f"Tabla resultados : {RESULTS_TABLE}")
logger.info(f"Directorio input : {INPUT_DIR}")
logger.info(f"Criterios cargados: {sorted(VALID_CRITERIA)}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Utilidades SQL

# COMMAND ----------

def _now() -> str:
    """Retorna la fecha/hora UTC actual en formato ISO 8601."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _esc(value: str) -> str:
    """Escapa comillas simples para sentencias SQL."""
    return (value or "").replace("'", "''")


def update_audit_result(
    id_resultado: int,
    criterio: str | None,
    detalles: str | None,
) -> None:
    """Actualiza el veredicto de auditoría sobre una fila de resultados."""
    crit_sql = f"'{_esc(criterio)}'" if criterio else "NULL"
    det_sql = f"'{_esc(detalles)}'" if detalles else "NULL"
    spark.sql(
        f"""
        UPDATE {_RESULTS_TABLE_SQL}
        SET criterio_fallido = {crit_sql},
            detalles_criterio_fallido = {det_sql}
        WHERE id_resultado = {int(id_resultado)}
        """
    )


# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Carga de insumos
# MAGIC
# MAGIC Reutiliza el parser de `mapping.md` (formato con secciones
# MAGIC `# Archivos` y `# Tablas`) y lo aplica también a `audit_mapping.md`.

# COMMAND ----------

_HINT_TABS_RE = re.compile(
    r"\[\s*(?:tabs?|hojas?|sheets?)\s*:\s*([^\]]+)\]", re.IGNORECASE
)
_HINT_COLS_RE = re.compile(
    r"\[\s*(?:columnas?|columns?|campos?|fields?)\s*:\s*([^\]]+)\]",
    re.IGNORECASE,
)


def _split_hint_list(raw: str) -> list:
    return [
        item.strip().strip("\"'`")
        for item in raw.split(",")
        if item.strip()
    ]


def parse_hints(description: str) -> dict:
    hints: dict = {"tabs": [], "columns": []}
    tabs_match = _HINT_TABS_RE.search(description)
    if tabs_match:
        hints["tabs"] = _split_hint_list(tabs_match.group(1))
    cols_match = _HINT_COLS_RE.search(description)
    if cols_match:
        hints["columns"] = _split_hint_list(cols_match.group(1))
    return hints


def _qualify(table_fqn: str) -> str:
    return ".".join(f"`{p}`" for p in table_fqn.split("."))


def parse_mapping_file(filepath: str) -> dict:
    """Lee un archivo de mapping y devuelve {archivos: [...], tablas: [...]}."""
    result = {"archivos": [], "tablas": []}
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
        logger.info(
            f"  Cargado {os.path.basename(filepath)} ({len(content)} chars)"
        )
    except FileNotFoundError:
        logger.info(f"  No existe {filepath} — se omite")
        return result
    except Exception as exc:
        logger.error(f"  Error leyendo {filepath}: {exc}")
        return result

    current_section = None
    for raw_line in content.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#"):
            header = line.lstrip("#").strip().lower()
            if "archivo" in header:
                current_section = "archivos"
            elif "tabla" in header:
                current_section = "tablas"
            else:
                current_section = None
            continue
        if current_section is None or ":" not in line:
            continue

        name_part, _, description = line.partition(":")
        name = name_part.strip().strip("`").strip()
        description = description.strip()
        if not name or not description:
            continue
        if current_section == "archivos" and "." in name:
            result["archivos"].append(
                {"name": name, "description": description}
            )
        elif current_section == "tablas" and name.count(".") == 2:
            result["tablas"].append(
                {"name": name, "description": description}
            )
    return result


def load_file_content(filepath: str, hints: dict | None = None) -> str:
    hints = hints or {}
    ext = os.path.splitext(filepath)[1].lower()

    if ext == ".docx":
        from docx import Document
        doc = Document(filepath)
        return "\n".join(p.text for p in doc.paragraphs if p.text.strip())

    if ext in (".tsv", ".csv"):
        sep = "\t" if ext == ".tsv" else ","
        df = pd.read_csv(filepath, sep=sep, encoding="utf-8")
        return df.to_string(index=False, max_rows=200)

    if ext in (".txt", ".md", ".json", ".yaml", ".yml"):
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()

    if ext in (".xls", ".xlsx"):
        tabs = hints.get("tabs", [])
        if not tabs:
            df = pd.read_excel(filepath)
            return df.to_string(index=False, max_rows=200)
        parts: list = []
        for tab in tabs:
            try:
                df = pd.read_excel(filepath, sheet_name=tab)
                parts.append(f"--- Hoja: {tab} ---")
                parts.append(df.to_string(index=False, max_rows=200))
            except Exception as exc:
                logger.warning(
                    f"    ⚠ Hoja '{tab}' no disponible: {str(exc)[:120]}"
                )
        return "\n".join(parts)

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def load_table_content(table_fqn: str, hints: dict | None = None) -> str:
    hints = hints or {}
    columns = hints.get("columns", [])
    qualified = _qualify(table_fqn)
    if columns:
        col_list = ", ".join(f"`{c}`" for c in columns)
        query = f"SELECT {col_list} FROM {qualified} LIMIT 200"
    else:
        query = f"SELECT * FROM {qualified} LIMIT 200"
    sample_df = spark.sql(query).toPandas()
    if sample_df.empty:
        return "(tabla vacía — sin registros)"
    return sample_df.to_string(index=False, max_colwidth=200)


def load_insumos(input_dir: str, mapping_files: list) -> dict:
    """Carga insumos de uno o más archivos mapping.md."""
    insumos: dict = {}
    for mapping_filename in mapping_files:
        filepath = f"{input_dir}/{mapping_filename}"
        mapping = parse_mapping_file(filepath)

        for entry in mapping["archivos"]:
            file_path = f"{input_dir}/{entry['name']}"
            hints = parse_hints(entry["description"])
            try:
                content = load_file_content(file_path, hints=hints)
                insumos[entry["name"]] = {
                    "description": entry["description"],
                    "content": content,
                    "size": len(content),
                    "kind": "archivo",
                    "source": mapping_filename,
                }
                logger.info(
                    f"    ✓ Archivo: {entry['name']} "
                    f"({len(content)} chars) [{mapping_filename}]"
                )
            except FileNotFoundError:
                logger.warning(
                    f"    ⚠ Archivo no encontrado: {entry['name']}"
                )
            except Exception as exc:
                logger.error(
                    f"    ✗ Error cargando {entry['name']}: {exc}"
                )

        for entry in mapping["tablas"]:
            table_fqn = entry["name"]
            hints = parse_hints(entry["description"])
            try:
                content = load_table_content(table_fqn, hints=hints)
                insumos[table_fqn] = {
                    "description": entry["description"],
                    "content": content,
                    "size": len(content),
                    "kind": "tabla",
                    "source": mapping_filename,
                }
                logger.info(
                    f"    ✓ Tabla: {table_fqn} "
                    f"({len(content)} chars) [{mapping_filename}]"
                )
            except Exception as exc:
                logger.warning(
                    f"    ⚠ No se pudo cargar '{table_fqn}': "
                    f"{str(exc)[:200]}"
                )
    return insumos


logger.info("ETAPA: Carga de insumos (mapping.md + audit_mapping.md)")
INSUMOS = load_insumos(INPUT_DIR, ["mapping.md", "audit_mapping.md"])
logger.info(f"  Total insumos cargados: {len(INSUMOS)}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Motor de contexto dinámico (idéntico a generate_comments)

# COMMAND ----------

MAX_CONTEXT_CHARS = 30_000


def _score_relevance(
    name: str,
    description: str,
    content: str,
    kind: str,
    schema_name: str,
    table_name: str = "",
) -> float:
    score = 1.0
    haystack = (description + " " + name + " " + content[:500]).lower()
    if schema_name and schema_name.lower() in haystack:
        score += 10.0
    if table_name and table_name.lower() in haystack:
        score += 15.0
    if kind == "tabla":
        score += 4.0
    else:
        ext = os.path.splitext(name)[1].lower()
        if ext == ".docx":
            score += 3.0
        elif ext == ".tsv":
            score += 2.0
        elif ext in (".md", ".txt"):
            score += 1.5
    keywords = (
        "definic", "ejemplo", "comentario", "tabla", "columna",
        "campo", "negocio", "instruccion", "regla", "glosario",
        "taxonom", "politica", "lineamiento", "estilo",
    )
    for kw in keywords:
        if kw in description.lower():
            score += 1.0
    return score


def build_dynamic_context(
    insumos: dict,
    schema_name: str,
    table_name: str = "",
    max_chars: int = MAX_CONTEXT_CHARS,
) -> str:
    if not insumos:
        return ""
    scored = []
    for name, data in insumos.items():
        score = _score_relevance(
            name, data["description"], data["content"], data["kind"],
            schema_name, table_name,
        )
        scored.append((score, name, data))
    scored.sort(key=lambda x: x[0], reverse=True)

    parts = []
    chars_used = 0
    for score, name, data in scored:
        label = "Tabla" if data["kind"] == "tabla" else "Insumo"
        header = (
            f"\n--- {label}: {name} "
            f"(Propósito: {data['description']}) ---\n"
        )
        content = data["content"]
        entry_size = len(header) + len(content)
        if chars_used + entry_size > max_chars:
            remaining = max_chars - chars_used - len(header) - 50
            if remaining > 500:
                content = (
                    content[:remaining] + "\n[... contenido truncado]"
                )
                parts.append(header + content)
                chars_used += len(header) + len(content)
            break
        parts.append(header + content)
        chars_used += entry_size
    return "\n".join(parts)


# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Llamada al modelo auditor

# COMMAND ----------

CRITERIA_BLOCK = format_for_prompt()


def _call_audit_model(prompt: str, max_tokens: int = 400) -> str:
    response = deploy_client.predict(
        endpoint=AUDIT_MODEL_ENDPOINT,
        inputs={
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.0,
        },
    )
    return response["choices"][0]["message"]["content"].strip()


_JSON_BLOCK_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)


def _parse_audit_response(raw: str) -> dict:
    """Extrae un JSON {ok, criterio, detalles} de la respuesta del modelo."""
    if not raw:
        return {"ok": True, "criterio": None, "detalles": None}

    candidate = raw.strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if candidate.lower().startswith("json"):
            candidate = candidate[4:].strip()

    match = _JSON_BLOCK_RE.search(candidate)
    if match:
        candidate = match.group(0)

    try:
        data = json.loads(candidate)
    except Exception:
        logger.warning(
            f"    ⚠ Respuesta del auditor no parseable como JSON: "
            f"{raw[:200]}"
        )
        return {"ok": True, "criterio": None, "detalles": None}

    ok = bool(data.get("ok", True))
    criterio = data.get("criterio")
    detalles = data.get("detalles")

    if ok:
        return {"ok": True, "criterio": None, "detalles": None}

    if criterio not in VALID_CRITERIA:
        logger.warning(
            f"    ⚠ Criterio devuelto fuera del catálogo: '{criterio}' "
            "— se ignora la observación"
        )
        return {"ok": True, "criterio": None, "detalles": None}

    return {
        "ok": False,
        "criterio": criterio,
        "detalles": (detalles or "").strip() or None,
    }


def audit_row(
    nivel: str,
    catalogo: str,
    esquema: str,
    tabla: str,
    columna: str,
    comentario: str,
    context: str,
) -> dict:
    """Audita un comentario y devuelve {ok, criterio, detalles}."""
    target_desc = (
        f"Esquema `{catalogo}`.`{esquema}`"
        if nivel == "esquema"
        else (
            f"Tabla `{catalogo}`.`{esquema}`.`{tabla}`"
            if nivel == "tabla"
            else f"Columna `{catalogo}`.`{esquema}`.`{tabla}`.`{columna}`"
        )
    )

    prompt = (
        "Eres un auditor experto en documentación de datos. Tu tarea es "
        "evaluar si un comentario generado por IA está apegado "
        "EXCLUSIVAMENTE a los insumos provistos abajo. Usa únicamente la "
        "información del contexto; no apliques conocimiento previo.\n\n"
        "=== CRITERIOS DE FALLA (elige a lo sumo UNO) ===\n"
        f"{CRITERIA_BLOCK}\n\n"
        "=== INSUMOS ===\n"
        f"{context}\n\n"
        "=== OBJETO AUDITADO ===\n"
        f"Nivel  : {nivel}\n"
        f"Objeto : {target_desc}\n"
        f"Comentario generado:\n\"\"\"\n{comentario}\n\"\"\"\n\n"
        "=== INSTRUCCIONES DE RESPUESTA ===\n"
        "Responde ÚNICAMENTE con un JSON válido, sin texto adicional, sin "
        "markdown, con la siguiente forma exacta:\n"
        '{"ok": true|false, "criterio": "ID_DEL_CRITERIO"|null, '
        '"detalles": "explicación breve en español"|null}\n\n'
        "- Si el comentario está apegado a los insumos: "
        '{"ok": true, "criterio": null, "detalles": null}.\n'
        "- Si falla EXACTAMENTE UN criterio: ok=false, criterio=ID exacto "
        "del catálogo de arriba, detalles=máx 300 caracteres explicando "
        "qué encontraste y citando el insumo o la parte del comentario "
        "que lo evidencia.\n"
        "- Si dudás entre varios criterios, elige el más grave / "
        "el de mayor impacto."
    )

    try:
        raw = _call_audit_model(prompt)
    except Exception as exc:
        logger.error(f"    ✗ Error invocando modelo: {exc}")
        return {"ok": True, "criterio": None, "detalles": None}

    return _parse_audit_response(raw)


# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Resolución de la ejecución a auditar

# COMMAND ----------

if not ID_EJECUCION:
    try:
        ID_EJECUCION = (
            dbutils.jobs.taskValues.get(
                taskKey="generate_comments",
                key="exec_id",
                default="",
                debugValue="",
            )
            or ""
        )
        if ID_EJECUCION:
            print(
                f"Usando id_ejecucion de la tarea generate_comments: "
                f"{ID_EJECUCION}"
            )
    except Exception:
        ID_EJECUCION = ""

filters = []
if ID_EJECUCION:
    filters.append(f"id_ejecucion = '{_esc(ID_EJECUCION)}'")
if AUDIT_ONLY_APPROVED:
    filters.append("status = 'aprobado'")
where_clause = " AND ".join(filters) if filters else "1=1"

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. Carga de filas a auditar

# COMMAND ----------

rows = spark.sql(
    f"""
    SELECT
        id_resultado,
        nombre_catalogo,
        nombre_esquema,
        nombre_tabla,
        nombre_columna,
        comentario
    FROM {_RESULTS_TABLE_SQL}
    WHERE {where_clause}
    ORDER BY nombre_catalogo, nombre_esquema, nombre_tabla, nombre_columna
    """
).collect()

logger.info(f"Filas a auditar: {len(rows)}")

if not rows:
    logger.warning("No hay filas para auditar. Fin.")
    dbutils.notebook.exit("0 filas auditadas")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 9. Ejecución de la auditoría

# COMMAND ----------

SEP = "=" * 60
logger.info(SEP)
logger.info("AUDITORÍA INICIADA")
logger.info(f"  Timestamp : {_now()} UTC")
logger.info(f"  Filas     : {len(rows)}")
logger.info(f"  Filtro    : {where_clause}")
logger.info(SEP)

stats = {"ok": 0, "observadas": 0, "errores": 0}
per_criterio: dict = {}

for row in rows:
    id_res = row["id_resultado"]
    catalogo = row["nombre_catalogo"] or ""
    esquema = row["nombre_esquema"] or ""
    tabla = row["nombre_tabla"] or ""
    columna = row["nombre_columna"] or ""
    comentario = row["comentario"] or ""

    if tabla == "__esquema__" and columna == "__esquema__":
        nivel = "esquema"
    elif columna == "__tabla__":
        nivel = "tabla"
    else:
        nivel = "columna"

    context = build_dynamic_context(
        INSUMOS,
        schema_name=esquema,
        table_name="" if nivel == "esquema" else tabla,
    )

    verdict = audit_row(
        nivel=nivel,
        catalogo=catalogo,
        esquema=esquema,
        tabla=tabla,
        columna=columna,
        comentario=comentario,
        context=context,
    )

    try:
        update_audit_result(
            id_res, verdict["criterio"], verdict["detalles"]
        )
    except Exception as exc:
        stats["errores"] += 1
        logger.error(f"  ✗ id_resultado={id_res} UPDATE falló: {exc}")
        continue

    target_label = (
        f"esquema {catalogo}.{esquema}"
        if nivel == "esquema"
        else (
            f"tabla {catalogo}.{esquema}.{tabla}"
            if nivel == "tabla"
            else f"columna {catalogo}.{esquema}.{tabla}.{columna}"
        )
    )

    if verdict["ok"]:
        stats["ok"] += 1
        logger.info(f"  ✓ OK     {target_label}")
    else:
        stats["observadas"] += 1
        per_criterio[verdict["criterio"]] = (
            per_criterio.get(verdict["criterio"], 0) + 1
        )
        detalles_preview = (verdict["detalles"] or "")[:120]
        logger.info(
            f"  ⚠ FALLA  {target_label} "
            f"[{verdict['criterio']}] {detalles_preview}"
        )

# COMMAND ----------

# MAGIC %md
# MAGIC ## 10. Resumen

# COMMAND ----------

total = stats["ok"] + stats["observadas"] + stats["errores"]
print("=" * 60)
print("RESUMEN DE AUDITORÍA")
print("=" * 60)
print(f"  Total auditadas  : {total}")
print(f"  OK               : {stats['ok']}")
print(f"  Con observación  : {stats['observadas']}")
print(f"  Errores UPDATE   : {stats['errores']}")
if per_criterio:
    print("\n  Por criterio:")
    for crit, n in sorted(per_criterio.items(), key=lambda x: -x[1]):
        print(f"    - {crit}: {n}")
print("=" * 60)

dbutils.notebook.exit(
    f"Auditadas: {total} | OK: {stats['ok']} | "
    f"Observadas: {stats['observadas']} | Errores: {stats['errores']}"
)
