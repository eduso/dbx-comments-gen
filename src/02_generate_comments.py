# Databricks notebook source
# MAGIC %md
# MAGIC # Generador de Comentarios v6
# MAGIC ## Documentación automática de esquemas, tablas y columnas con IA
# MAGIC
# MAGIC Genera comentarios de negocio en español para cada **esquema**, **tabla**
# MAGIC y **columna** listada en una **tabla de scope** de Unity Catalog,
# MAGIC usando un modelo fundacional vía Foundation Model API.
# MAGIC
# MAGIC ### Fuente del scope
# MAGIC
# MAGIC La lista de objetos a documentar se lee de una **tabla de control**
# MAGIC (parámetro `scope_table`) que debe contener tres columnas
# MAGIC configurables con el nombre del catálogo, esquema y tabla a procesar
# MAGIC (`scope_catalog_column`, `scope_schema_column`, `scope_table_column`).
# MAGIC Cada combinación distinta (catálogo, esquema, tabla) se procesa una
# MAGIC vez; los esquemas se comentan una sola vez aunque aparezcan varias
# MAGIC veces en la tabla de scope.
# MAGIC
# MAGIC ### Insumos de contexto
# MAGIC
# MAGIC El notebook lee `input/mapping.md`, que tiene dos secciones:
# MAGIC
# MAGIC - **`# Archivos`**: documentos del directorio `input/`
# MAGIC   (`.docx`, `.tsv`, `.csv`, `.xlsx`, `.txt`, `.md`, `.json`, `.yaml`).
# MAGIC - **`# Tablas`**: tablas de Unity Catalog
# MAGIC   (`catalogo.esquema.tabla: descripción`).
# MAGIC
# MAGIC Si una tabla no es accesible o no existe, se emite un warning y el
# MAGIC proceso continúa sin ella.
# MAGIC
# MAGIC ### Parámetros
# MAGIC
# MAGIC Todos los parámetros son obligatorios excepto `model_endpoint`, que
# MAGIC tiene un default.

# COMMAND ----------

# MAGIC %pip install python-docx mlflow openpyxl -q
# MAGIC dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Lectura y validación de parámetros

# COMMAND ----------

dbutils.widgets.text(
    "scope_table", "",
    "Tabla de scope (catalogo.esquema.tabla)",
)
dbutils.widgets.text(
    "scope_catalog_column", "",
    "Columna con el catálogo",
)
dbutils.widgets.text(
    "scope_schema_column", "",
    "Columna con el esquema",
)
dbutils.widgets.text(
    "scope_table_column", "",
    "Columna con la tabla",
)
dbutils.widgets.text(
    "model_endpoint",
    "databricks-claude-sonnet-4-5",
    "Modelo fundacional",
)
dbutils.widgets.text("results_catalog", "", "Catálogo de resultados")
dbutils.widgets.text("results_schema", "", "Esquema de resultados")
dbutils.widgets.dropdown(
    "enable_sampling", "no", ["no", "yes"], "Sampling de datos"
)
dbutils.widgets.text("sampling_pct", "", "Porcentaje de sampling (1-100)")

SCOPE_TABLE = dbutils.widgets.get("scope_table").strip()
SCOPE_CATALOG_COLUMN = dbutils.widgets.get("scope_catalog_column").strip()
SCOPE_SCHEMA_COLUMN = dbutils.widgets.get("scope_schema_column").strip()
SCOPE_TABLE_COLUMN = dbutils.widgets.get("scope_table_column").strip()
MODEL_ENDPOINT = dbutils.widgets.get("model_endpoint").strip()
RESULTS_CATALOG = dbutils.widgets.get("results_catalog").strip()
RESULTS_SCHEMA = dbutils.widgets.get("results_schema").strip()
ENABLE_SAMPLING = (
    dbutils.widgets.get("enable_sampling").strip().lower() == "yes"
)

_required = {
    "scope_table": SCOPE_TABLE,
    "scope_catalog_column": SCOPE_CATALOG_COLUMN,
    "scope_schema_column": SCOPE_SCHEMA_COLUMN,
    "scope_table_column": SCOPE_TABLE_COLUMN,
    "model_endpoint": MODEL_ENDPOINT,
    "results_catalog": RESULTS_CATALOG,
    "results_schema": RESULTS_SCHEMA,
}
_missing = [k for k, v in _required.items() if not v]
if _missing:
    raise ValueError(
        f"Parámetros obligatorios sin valor: {', '.join(_missing)}"
    )

if SCOPE_TABLE.count(".") != 2:
    raise ValueError(
        "'scope_table' debe tener formato 'catalogo.esquema.tabla'."
    )

if ENABLE_SAMPLING:
    try:
        SAMPLING_PCT = int(dbutils.widgets.get("sampling_pct").strip())
        if not 1 <= SAMPLING_PCT <= 100:
            raise ValueError
    except ValueError:
        raise ValueError(
            "Si enable_sampling=yes, 'sampling_pct' debe ser entero 1-100."
        )
else:
    SAMPLING_PCT = 10

print("=" * 60)
print("PARÁMETROS DE EJECUCIÓN")
print("=" * 60)
print(f"  Tabla scope         : {SCOPE_TABLE}")
print(f"  Columna catálogo    : {SCOPE_CATALOG_COLUMN}")
print(f"  Columna esquema     : {SCOPE_SCHEMA_COLUMN}")
print(f"  Columna tabla       : {SCOPE_TABLE_COLUMN}")
print(f"  Modelo              : {MODEL_ENDPOINT}")
print(f"  Resultados en       : {RESULTS_CATALOG}.{RESULTS_SCHEMA}")
print(f"  Sampling habilitado : {ENABLE_SAMPLING}")
print(f"  Porcentaje sampling : {SAMPLING_PCT}%")
print("=" * 60)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Configuración del entorno

# COMMAND ----------

import logging
import os
import re
import uuid
from datetime import datetime, timezone

import pandas as pd
from mlflow.deployments import get_deploy_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("comments_generator_v5")

EXEC_TABLE = f"{RESULTS_CATALOG}.{RESULTS_SCHEMA}.ejecuciones"
RESULTS_TABLE = f"{RESULTS_CATALOG}.{RESULTS_SCHEMA}.resultados"

_notebook_path = (
    dbutils.notebook.entry_point.getDbutils()
    .notebook()
    .getContext()
    .notebookPath()
    .get()
)
PROJECT_ROOT = os.path.dirname(os.path.dirname(_notebook_path))
INPUT_DIR = f"/Workspace{PROJECT_ROOT}/input"

deploy_client = get_deploy_client("databricks")

logger.info(f"Endpoint         : {MODEL_ENDPOINT}")
logger.info(f"Tabla ejecuciones: {EXEC_TABLE}")
logger.info(f"Tabla resultados : {RESULTS_TABLE}")
logger.info(f"Directorio input : {INPUT_DIR}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Funciones de persistencia

# COMMAND ----------

_EXEC_TABLE_SQL = ".".join(f"`{p}`" for p in EXEC_TABLE.split("."))
_RESULTS_TABLE_SQL = ".".join(f"`{p}`" for p in RESULTS_TABLE.split("."))


def _now() -> str:
    """Retorna la fecha/hora UTC actual en formato ISO 8601."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _esc(value: str) -> str:
    """Escapa comillas simples para sentencias SQL."""
    return (value or "").replace("'", "''")


def insert_ejecucion(exec_id: str, estado: str) -> None:
    """Registra una nueva ejecución en estado inicial."""
    spark.sql(
        f"""
        INSERT INTO {_EXEC_TABLE_SQL}
            (id_ejecucion, fecha_ejecucion, estado, resultado)
        VALUES
            ('{exec_id}', TIMESTAMP '{_now()}',
             '{_esc(estado)}', NULL)
        """
    )
    logger.info(f"  Ejecución registrada: {exec_id} [{estado}]")


def update_ejecucion(
    exec_id: str, estado: str, resultado: str | None = None
) -> None:
    """Actualiza el estado y resultado de una ejecución."""
    resultado_sql = (
        f"'{_esc(resultado)}'" if resultado is not None else "NULL"
    )
    spark.sql(
        f"""
        UPDATE {_EXEC_TABLE_SQL}
        SET estado = '{_esc(estado)}',
            fecha_ejecucion = TIMESTAMP '{_now()}',
            resultado = {resultado_sql}
        WHERE id_ejecucion = '{exec_id}'
        """
    )


def insert_resultado(
    exec_id: str,
    nombre_catalogo: str,
    nombre_esquema: str,
    nombre_tabla: str,
    nombre_columna: str,
    comentario: str,
) -> None:
    """Inserta un comentario generado (status='aprobado' por default)."""
    spark.sql(
        f"""
        INSERT INTO {_RESULTS_TABLE_SQL}
            (id_ejecucion, fecha_resultado, nombre_catalogo,
             nombre_esquema, nombre_tabla, nombre_columna, comentario,
             status, user_comments,
             criterio_fallido, detalles_criterio_fallido)
        VALUES (
            '{exec_id}', TIMESTAMP '{_now()}',
            '{_esc(nombre_catalogo)}',
            '{_esc(nombre_esquema)}', '{_esc(nombre_tabla)}',
            '{_esc(nombre_columna)}', '{_esc(comentario)}',
            'aprobado', NULL,
            NULL, NULL
        )
        """
    )


# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Carga de insumos
# MAGIC
# MAGIC Lee `input/mapping.md` con dos secciones:
# MAGIC
# MAGIC - `# Archivos`: archivos del directorio `input/`.
# MAGIC - `# Tablas`: tablas de Unity Catalog.
# MAGIC
# MAGIC ### Hints opcionales en la descripción
# MAGIC
# MAGIC Dentro de la descripción se pueden incluir hints entre corchetes
# MAGIC para acotar qué cargar:
# MAGIC
# MAGIC - **Excel**: `[tabs: tab1, tab2]` para leer solo esas hojas.
# MAGIC - **Tablas**: `[columnas: col1, col2]` para leer solo esas columnas.
# MAGIC
# MAGIC Alias aceptados (case-insensitive):
# MAGIC `tabs`/`hojas`/`sheets`, `columnas`/`columns`/`campos`/`fields`.

# COMMAND ----------

logger.info("ETAPA: Carga de insumos desde mapping.md")


_HINT_TABS_RE = re.compile(
    r"\[\s*(?:tabs?|hojas?|sheets?)\s*:\s*([^\]]+)\]", re.IGNORECASE
)
_HINT_COLS_RE = re.compile(
    r"\[\s*(?:columnas?|columns?|campos?|fields?)\s*:\s*([^\]]+)\]",
    re.IGNORECASE,
)


def _split_hint_list(raw: str) -> list:
    """Split de '[a, "b", c]' a ['a', 'b', 'c']."""
    return [
        item.strip().strip("\"'`")
        for item in raw.split(",")
        if item.strip()
    ]


def parse_hints(description: str) -> dict:
    """Extrae hints estructurados de la descripción de un insumo.

    Soporta '[tabs: a, b]' y '[columnas: x, y]' (case-insensitive).

    Returns:
        dict con keys 'tabs' (list) y 'columns' (list).
    """
    hints: dict = {"tabs": [], "columns": []}
    tabs_match = _HINT_TABS_RE.search(description)
    if tabs_match:
        hints["tabs"] = _split_hint_list(tabs_match.group(1))
    cols_match = _HINT_COLS_RE.search(description)
    if cols_match:
        hints["columns"] = _split_hint_list(cols_match.group(1))
    return hints


def _qualify(table_fqn: str) -> str:
    """Convierte 'a.b.c' → '`a`.`b`.`c`' para SQL seguro."""
    return ".".join(f"`{p}`" for p in table_fqn.split("."))


def parse_mapping_file(input_dir: str) -> dict:
    """Lee mapping.md y separa entradas por sección.

    Returns:
        dict con keys 'archivos' (list) y 'tablas' (list). Cada item:
        {'name': str, 'description': str}.
    """
    result = {"archivos": [], "tablas": []}
    mapping_path = f"{input_dir}/mapping.md"

    try:
        with open(mapping_path, "r", encoding="utf-8") as f:
            content = f.read()
        logger.info(f"  Cargado mapping.md ({len(content)} chars)")
    except FileNotFoundError:
        logger.warning(
            f"  No se encontró {mapping_path} — sin insumos de contexto"
        )
        return result
    except Exception as exc:
        logger.error(f"  Error leyendo mapping.md: {exc}")
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

    logger.info(
        f"  Archivos: {len(result['archivos'])}, "
        f"Tablas: {len(result['tablas'])}"
    )
    return result


def load_file_content(filepath: str, hints: dict | None = None) -> str:
    """Carga el contenido de un archivo según su extensión.

    Args:
        filepath: ruta absoluta al archivo.
        hints: dict opcional con 'tabs' (list) para Excel multi-hoja.
    """
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
                    f"    ⚠ Hoja '{tab}' no disponible en "
                    f"{os.path.basename(filepath)}: {str(exc)[:120]}"
                )
        return "\n".join(parts)

    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        logger.warning(f"  Formato no soportado: {filepath}")
        return ""


def load_table_content(table_fqn: str, hints: dict | None = None) -> str:
    """Carga el contenido de una tabla UC como texto tabular.

    Args:
        table_fqn: nombre fully-qualified 'catalogo.esquema.tabla'.
        hints: dict opcional con 'columns' (list) para seleccionar
            solo esas columnas.
    """
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


def load_all_insumos(input_dir: str) -> dict:
    """Carga todos los insumos referenciados en mapping.md.

    Returns:
        Dict indexado por nombre. Cada valor:
        {'description': str, 'content': str, 'size': int, 'kind': str}.
    """
    mapping = parse_mapping_file(input_dir)
    insumos: dict = {}

    for entry in mapping["archivos"]:
        filepath = f"{input_dir}/{entry['name']}"
        hints = parse_hints(entry["description"])
        try:
            content = load_file_content(filepath, hints=hints)
            insumos[entry["name"]] = {
                "description": entry["description"],
                "content": content,
                "size": len(content),
                "kind": "archivo",
                "hints": hints,
            }
            extras = []
            if hints["tabs"]:
                extras.append(f"tabs={hints['tabs']}")
            extras_str = f" [{', '.join(extras)}]" if extras else ""
            logger.info(
                f"    ✓ Archivo: {entry['name']} "
                f"({len(content)} chars){extras_str}"
            )
        except FileNotFoundError:
            logger.warning(f"    ⚠ Archivo no encontrado: {entry['name']}")
        except Exception as exc:
            logger.error(f"    ✗ Error cargando {entry['name']}: {exc}")

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
                "hints": hints,
            }
            extras = []
            if hints["columns"]:
                extras.append(f"columns={hints['columns']}")
            extras_str = f" [{', '.join(extras)}]" if extras else ""
            logger.info(
                f"    ✓ Tabla: {table_fqn} "
                f"({len(content)} chars){extras_str}"
            )
        except Exception as exc:
            logger.warning(
                f"    ⚠ No se pudo cargar tabla '{table_fqn}': "
                f"{str(exc)[:200]}"
            )

    return insumos


INSUMOS = load_all_insumos(INPUT_DIR)
logger.info(f"  Total insumos cargados: {len(INSUMOS)}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Motor de contexto dinámico
# MAGIC
# MAGIC Para cada esquema/tabla:
# MAGIC
# MAGIC 1. Calcula un **score de relevancia** por insumo.
# MAGIC 2. Ordena por score descendente.
# MAGIC 3. Incluye insumos hasta el límite de **30K caracteres**
# MAGIC    (~7.500 tokens), truncando el último si no cabe.

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
    """Calcula el score de relevancia de un insumo."""
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
        "taxonom",
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
    """Construye el bloque de contexto priorizando insumos relevantes."""
    if not insumos:
        return ""

    scored = []
    for name, data in insumos.items():
        score = _score_relevance(
            name,
            data["description"],
            data["content"],
            data["kind"],
            schema_name,
            table_name,
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
                    content[:remaining]
                    + "\n[... contenido truncado]"
                )
                parts.append(header + content)
                chars_used += len(header) + len(content)
                logger.info(
                    f"    Contexto truncado: {name} "
                    f"({remaining} de {data['size']} chars)"
                )
            else:
                logger.info(
                    f"    Contexto omitido por límite: {name} "
                    f"(score={score:.1f})"
                )
            break

        parts.append(header + content)
        chars_used += entry_size

    logger.info(
        f"    Contexto construido: {chars_used} chars, "
        f"{len(parts)} insumo(s)"
    )
    return "\n".join(parts)


# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Sampling de datos
# MAGIC
# MAGIC Cuando `enable_sampling=yes`, toma una muestra aleatoria de cada
# MAGIC tabla para enriquecer el prompt.

# COMMAND ----------

def get_table_sample(
    catalog: str,
    schema: str,
    table: str,
    sampling_pct: int = SAMPLING_PCT,
) -> str:
    """Obtiene una muestra aleatoria de la tabla como contexto."""
    fqn = f"`{catalog}`.`{schema}`.`{table}`"
    try:
        count_row = spark.sql(
            f"SELECT COUNT(*) AS cnt FROM {fqn}"
        ).collect()
        total_rows = count_row[0]["cnt"]

        if total_rows == 0:
            logger.info(f"      Sampling {table}: tabla vacía")
            return "(tabla vacía — sin registros)"

        if total_rows <= 500:
            sample_size = total_rows
            logger.info(
                f"      Sampling {table}: {total_rows} registros "
                "(todos — tabla pequeña)"
            )
        else:
            fraction = sampling_pct / 100.0
            sample_size = max(int(total_rows * fraction), 1)
            logger.info(
                f"      Sampling {table}: {sample_size} de "
                f"{total_rows} registros ({sampling_pct}%)"
            )

        sample_df = spark.sql(
            f"SELECT * FROM {fqn} ORDER BY RAND() LIMIT {sample_size}"
        )
        pdf = sample_df.toPandas().iloc[:30, :20]
        return pdf.to_string(index=False, max_colwidth=50)

    except Exception as exc:
        logger.warning(
            f"      ⚠ Error obteniendo muestra de {fqn}: {exc}"
        )
        return f"(error al obtener muestra: {str(exc)[:100]})"


# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Generación de comentarios

# COMMAND ----------

def _call_model(prompt: str, max_tokens: int = 500) -> str:
    """Invoca el modelo fundacional vía Foundation Model API."""
    response = deploy_client.predict(
        endpoint=MODEL_ENDPOINT,
        inputs={
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0.2,
        },
    )
    return response["choices"][0]["message"]["content"].strip()


def generate_schema_comment(
    schema_name: str,
    tables_in_schema: list,
    context: str,
) -> str:
    """Genera un comentario de negocio para un esquema."""
    tables_list = ", ".join(tables_in_schema[:30])
    prompt = (
        "Eres un experto en documentación de datos orientada a usuarios "
        "de negocio de una organización.\n"
        + context
        + "\nGenera una definición de negocio para el siguiente esquema:"
        + f"\n\n- Esquema: {schema_name}"
        + f"\n- Tablas contenidas: {tables_list}\n\n"
        + "La definición debe:\n"
        + "- Utilizar solo la información dada en el contexto, no "
          "conocimiento previo.\n"
        + "- Explicar el propósito del esquema y el dominio de negocio.\n"
        + "- Mencionar los principales tipos de datos que contiene.\n"
        + "- Estar en español, lenguaje claro para usuarios de negocio.\n"
        + "- Tener máximo 500 caracteres.\n"
        + "- Responde ÚNICAMENTE con la definición, sin comillas."
    )
    return _call_model(prompt)


def generate_table_comment(
    schema_name: str,
    schema_comment: str,
    table_name: str,
    context: str,
    sample_data: str = "",
) -> str:
    """Genera un comentario de negocio para una tabla."""
    context_block = ""
    if schema_comment:
        context_block = (
            f"\nContexto del esquema '{schema_name}': {schema_comment}\n"
        )

    sample_block = ""
    if sample_data:
        sample_block = (
            "\nMuestra de datos reales de la tabla:\n"
            f"```\n{sample_data[:3000]}\n```\n"
        )

    prompt = (
        "Eres un experto en documentación de datos orientada a usuarios "
        "de negocio de una organización.\n"
        + context
        + context_block
        + sample_block
        + "\nGenera una definición de negocio clara y completa para la "
          "siguiente tabla:"
        + f"\n\n- Tabla: {table_name}\n\n"
        + "La definición debe:\n"
        + "- Utilizar solo la información dada en el contexto, no "
          "conocimiento previo.\n"
        + "- Explicar el nivel de granularidad de la información.\n"
        + "- Indicar los principales usos en el negocio.\n"
        + "- Mencionar reglas o lógicas de negocio relevantes.\n"
        + "- Estar en español, lenguaje claro para usuarios de negocio.\n"
        + "- Tener máximo 500 caracteres.\n"
        + "- Responde ÚNICAMENTE con la definición, sin comillas."
    )
    return _call_model(prompt)


def generate_column_comment(
    schema_name: str,
    schema_comment: str,
    table_name: str,
    table_comment: str,
    column_name: str,
    data_type: str,
    context: str,
    sample_data: str = "",
) -> str:
    """Genera un comentario de negocio para una columna."""
    context_block = ""
    if schema_comment:
        context_block += f"\nContexto del esquema: {schema_comment}"
    if table_comment:
        context_block += (
            f"\nContexto de la tabla '{table_name}': {table_comment}"
        )
    if context_block:
        context_block += "\n"

    sample_block = ""
    if sample_data:
        sample_block = (
            "\nMuestra de datos reales de la tabla:\n"
            f"```\n{sample_data[:3000]}\n```\n"
        )

    prompt = (
        "Eres un experto en documentación de datos orientada a usuarios "
        "de negocio de una organización.\n"
        + context
        + context_block
        + sample_block
        + "\nGenera una definición de negocio clara y completa para la "
          "siguiente columna:"
        + f"\n\n- Tabla  : {table_name}"
        + f"\n- Columna: {column_name}"
        + f"\n- Tipo   : {data_type}\n\n"
        + "La definición debe:\n"
        + "- Describir el propósito o contenido del campo.\n"
        + "- Usar nombres funcionales si los insumos los proporcionan.\n"
        + "- Incluir reglas de negocio del campo si el nombre lo sugiere.\n"
        + "- Explicar posibles valores si es catálogo, indicador o código.\n"
        + "- Estar en español, lenguaje claro y accesible.\n"
        + "- Tener máximo 300 caracteres.\n"
        + "- Responde ÚNICAMENTE con la definición, sin comillas."
    )
    return _call_model(prompt, max_tokens=400)


# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. Descubrimiento desde la tabla de scope
# MAGIC
# MAGIC Lee la tabla configurada en `scope_table` y agrupa las filas en una
# MAGIC estructura `catalogo → esquema → tablas`. Luego enriquece cada
# MAGIC esquema/tabla con su `comment` y la lista de columnas usando comandos
# MAGIC `DESCRIBE` sobre cada objeto.
# MAGIC
# MAGIC > **Permisos:** se usa `DESCRIBE SCHEMA EXTENDED` / `DESCRIBE TABLE
# MAGIC > EXTENDED` en lugar de `information_schema`. Estos comandos solo
# MAGIC > requieren privilegio sobre el objeto descrito (`USE CATALOG` +
# MAGIC > `USE SCHEMA` + `SELECT`), no acceso a `information_schema` ni a
# MAGIC > `system.*`.

# COMMAND ----------

logger.info("ETAPA: Descubrimiento desde tabla de scope")


def load_scope_targets(
    scope_table: str,
    catalog_column: str,
    schema_column: str,
    table_column: str,
) -> dict:
    """Lee la tabla de scope y devuelve {catalog: {schema: [tablas]}}.

    Las filas con valores nulos o vacíos en cualquier columna se omiten.
    """
    qualified = _qualify(scope_table)
    rows = spark.sql(
        f"""
        SELECT DISTINCT
            `{catalog_column}` AS catalog_name,
            `{schema_column}`  AS schema_name,
            `{table_column}`   AS table_name
        FROM {qualified}
        WHERE `{catalog_column}` IS NOT NULL
          AND `{schema_column}`  IS NOT NULL
          AND `{table_column}`   IS NOT NULL
        ORDER BY catalog_name, schema_name, table_name
        """
    ).collect()

    targets: dict = {}
    for row in rows:
        cat = (row["catalog_name"] or "").strip()
        sch = (row["schema_name"] or "").strip()
        tbl = (row["table_name"] or "").strip()
        if not cat or not sch or not tbl:
            continue
        targets.setdefault(cat, {}).setdefault(sch, []).append(tbl)

    n_tables = sum(
        len(tables)
        for schemas in targets.values()
        for tables in schemas.values()
    )
    n_schemas = sum(len(schemas) for schemas in targets.values())
    logger.info(
        f"  Scope: {len(targets)} catálogo(s), "
        f"{n_schemas} esquema(s), {n_tables} tabla(s)"
    )
    return targets


def get_schema_comment(catalog: str, schema: str) -> str:
    """Lee el comentario actual del esquema vía DESCRIBE SCHEMA EXTENDED.

    No consulta information_schema: solo requiere privilegio sobre el esquema.
    """
    try:
        rows = spark.sql(
            f"DESCRIBE SCHEMA EXTENDED `{catalog}`.`{schema}`"
        ).collect()
        # Devuelve pares (database_description_item, value); el comentario
        # está en la fila cuyo item es 'Comment'.
        for row in rows:
            if (row[0] or "").strip() == "Comment":
                return (row[1] or "").strip()
        return ""
    except Exception as exc:
        logger.warning(
            f"  ⚠ No se pudo leer comentario del esquema "
            f"{catalog}.{schema}: {str(exc)[:120]}"
        )
        return ""


def discover_columns_for_tables(
    catalog: str, schema: str, table_names: list
) -> dict:
    """Descubre columnas y comentario de un subconjunto de tablas.

    Usa DESCRIBE TABLE EXTENDED por tabla en lugar de information_schema:
    solo requiere privilegio sobre cada objeto descrito. El orden natural
    devuelto por DESCRIBE preserva el orden de las columnas.
    """
    if not table_names:
        return {}

    tables: dict = {}
    for tbl in table_names:
        fqn = _qualify(f"{catalog}.{schema}.{tbl}")
        try:
            rows = spark.sql(f"DESCRIBE TABLE EXTENDED {fqn}").collect()
        except Exception as exc:
            logger.warning(
                f"    ⚠ Tabla no accesible: `{catalog}`.`{schema}`.`{tbl}` — "
                f"se omitirá ({str(exc)[:120]})"
            )
            continue

        columns: list = []
        table_comment = ""
        in_columns = True
        for row in rows:
            col_name = (row["col_name"] or "").strip()
            data_type = (row["data_type"] or "").strip()
            if in_columns:
                # El bloque de columnas termina en la primera fila vacía o
                # de sección ('# Partition Information', '# Detailed...').
                if not col_name or col_name.startswith("#"):
                    in_columns = False
                    continue
                columns.append({"name": col_name, "type": data_type})
            elif col_name == "Comment":
                # En la sección de detalle, el valor va en la 2a columna.
                table_comment = data_type

        if not columns:
            logger.warning(
                f"    ⚠ Sin columnas legibles para "
                f"`{catalog}`.`{schema}`.`{tbl}` — se omitirá"
            )
            continue

        tables[tbl] = {"comment": table_comment, "columns": columns}

    total_cols = sum(len(t["columns"]) for t in tables.values())
    logger.info(
        f"    {catalog}.{schema}: {len(tables)} tabla(s), "
        f"{total_cols} columna(s)"
    )
    return tables


scope_targets = load_scope_targets(
    SCOPE_TABLE,
    SCOPE_CATALOG_COLUMN,
    SCOPE_SCHEMA_COLUMN,
    SCOPE_TABLE_COLUMN,
)

if not scope_targets:
    msg = (
        f"La tabla de scope '{SCOPE_TABLE}' no contiene filas válidas "
        f"con las columnas '{SCOPE_CATALOG_COLUMN}', "
        f"'{SCOPE_SCHEMA_COLUMN}', '{SCOPE_TABLE_COLUMN}'."
    )
    logger.error(msg)
    raise ValueError(msg)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 9. Ejecución principal

# COMMAND ----------

exec_id = str(uuid.uuid4())
SEP = "=" * 60

total_scope_schemas = sum(len(s) for s in scope_targets.values())
total_scope_tables = sum(
    len(tables)
    for schemas in scope_targets.values()
    for tables in schemas.values()
)

logger.info(SEP)
logger.info("EJECUCIÓN INICIADA")
logger.info(f"  ID        : {exec_id}")
logger.info(f"  Timestamp : {_now()} UTC")
logger.info(
    f"  Alcance   : {len(scope_targets)} catálogo(s), "
    f"{total_scope_schemas} esquema(s), "
    f"{total_scope_tables} tabla(s) — desde {SCOPE_TABLE}"
)
logger.info(f"  Modelo    : {MODEL_ENDPOINT}")
logger.info(
    f"  Sampling  : {'Sí (' + str(SAMPLING_PCT) + '%)' if ENABLE_SAMPLING else 'No'}"
)
logger.info(SEP)

insert_ejecucion(exec_id, "INICIADO")

try:
    total_schemas_ok = 0
    total_tables_ok = 0
    total_columns_ok = 0
    errors: list = []
    processed_schemas: set = set()

    for current_catalog, schemas_map in scope_targets.items():
        for current_schema, requested_tables in schemas_map.items():
            schema_key = (current_catalog, current_schema)

            logger.info(f"\n{'─' * 50}")
            logger.info(
                f"ETAPA: Procesando esquema "
                f"'{current_catalog}.{current_schema}'"
            )
            logger.info(f"{'─' * 50}")

            schema_context = build_dynamic_context(
                INSUMOS, schema_name=current_schema
            )

            tables = discover_columns_for_tables(
                current_catalog, current_schema, requested_tables
            )

            if not tables:
                logger.warning(
                    f"  Sin tablas accesibles en "
                    f"{current_catalog}.{current_schema} — saltando"
                )
                continue

            table_names = list(tables.keys())

            update_ejecucion(
                exec_id,
                "EN_PROCESO",
                f"Procesando esquema "
                f"'{current_catalog}.{current_schema}' — "
                f"{len(tables)} tabla(s)",
            )

            current_schema_comment = get_schema_comment(
                current_catalog, current_schema
            )

            if schema_key not in processed_schemas:
                try:
                    generated_schema_comment = generate_schema_comment(
                        schema_name=current_schema,
                        tables_in_schema=table_names,
                        context=schema_context,
                    )
                    insert_resultado(
                        exec_id,
                        current_catalog,
                        current_schema,
                        "__esquema__",
                        "__esquema__",
                        generated_schema_comment,
                    )
                    total_schemas_ok += 1
                    processed_schemas.add(schema_key)
                    logger.info(
                        f"  ✓ [ESQUEMA] {current_catalog}.{current_schema}: "
                        f"{generated_schema_comment[:100]}..."
                    )
                    if not current_schema_comment:
                        current_schema_comment = generated_schema_comment
                except Exception as exc:
                    errors.append(
                        f"{current_catalog}.{current_schema} [esquema]: "
                        f"{str(exc)[:200]}"
                    )
                    logger.error(
                        f"  ✗ Error en comentario de esquema: {exc}"
                    )

            for table_name, table_data in tables.items():
                n_cols = len(table_data["columns"])
                logger.info(
                    f"\n  TABLA: "
                    f"'{current_catalog}.{current_schema}.{table_name}' "
                    f"({n_cols} columnas)"
                )

                update_ejecucion(
                    exec_id,
                    "EN_PROCESO",
                    f"Procesando "
                    f"'{current_catalog}.{current_schema}.{table_name}' — "
                    f"{total_columns_ok} columnas completadas",
                )

                table_context = build_dynamic_context(
                    INSUMOS,
                    schema_name=current_schema,
                    table_name=table_name,
                )

                sample_data = ""
                if ENABLE_SAMPLING:
                    sample_data = get_table_sample(
                        current_catalog,
                        current_schema,
                        table_name,
                        SAMPLING_PCT,
                    )

                generated_table_comment = ""
                try:
                    generated_table_comment = generate_table_comment(
                        schema_name=current_schema,
                        schema_comment=current_schema_comment,
                        table_name=table_name,
                        context=table_context,
                        sample_data=sample_data,
                    )
                    insert_resultado(
                        exec_id,
                        current_catalog,
                        current_schema,
                        table_name,
                        "__tabla__",
                        generated_table_comment,
                    )
                    total_tables_ok += 1
                    logger.info(
                        f"    ✓ [TABLA] {table_name}: "
                        f"{generated_table_comment[:100]}..."
                    )
                except Exception as exc:
                    errors.append(
                        f"{current_catalog}.{current_schema}.{table_name} "
                        f"[tabla]: {str(exc)[:200]}"
                    )
                    logger.error(
                        f"    ✗ Error en comentario de tabla: {exc}"
                    )
                    generated_table_comment = table_data["comment"]

                context_for_columns = (
                    generated_table_comment or table_data["comment"]
                )

                for col in table_data["columns"]:
                    col_name = col["name"]
                    col_type = col["type"]
                    try:
                        comment = generate_column_comment(
                            schema_name=current_schema,
                            schema_comment=current_schema_comment,
                            table_name=table_name,
                            table_comment=context_for_columns,
                            column_name=col_name,
                            data_type=col_type,
                            context=table_context,
                            sample_data=sample_data,
                        )
                        insert_resultado(
                            exec_id,
                            current_catalog,
                            current_schema,
                            table_name,
                            col_name,
                            comment,
                        )
                        total_columns_ok += 1
                        logger.info(
                            f"      ✓ {col_name} ({col_type}): "
                            f"{comment[:90]}..."
                        )
                    except Exception as exc:
                        errors.append(
                            f"{current_catalog}.{current_schema}."
                            f"{table_name}.{col_name}: {str(exc)[:200]}"
                        )
                        logger.error(f"      ✗ Error en {col_name}: {exc}")

    logger.info(f"\n{SEP}")
    logger.info("ETAPA: Finalizando ejecución")

    if errors:
        estado_final = "COMPLETADO_CON_ERRORES"
        error_summary = "; ".join(errors[:5])
        resultado_final = (
            f"Completado con {len(errors)} error(es). "
            f"Esquemas: {total_schemas_ok}/{total_scope_schemas}. "
            f"Tablas: {total_tables_ok}/{total_scope_tables}. "
            f"Columnas: {total_columns_ok}. "
            f"Errores: {error_summary}"
        )
    else:
        estado_final = "COMPLETADO"
        resultado_final = (
            f"Exitoso. {total_schemas_ok} esquema(s), "
            f"{total_tables_ok} tabla(s) y {total_columns_ok} columna(s) "
            f"documentadas desde scope '{SCOPE_TABLE}'."
        )

    update_ejecucion(exec_id, estado_final, resultado_final)

    logger.info(f"  Estado   : {estado_final}")
    logger.info(f"  Resultado: {resultado_final}")
    logger.info(f"  Fin      : {_now()} UTC")
    logger.info(SEP)

    dbutils.jobs.taskValues.set(key="exec_id", value=exec_id)

except Exception as exc:
    error_msg = str(exc)[:500]
    update_ejecucion(
        exec_id, "ERROR", f"Error inesperado: {error_msg}"
    )
    logger.error(f"✗ Error fatal en ejecución {exec_id}: {error_msg}")
    raise
