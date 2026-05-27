"""Criterios de auditoría para comentarios generados.

Para agregar un criterio: añadir un dict nuevo a AUDIT_CRITERIA.
Para quitar uno: eliminar el dict correspondiente.
Para modificar la descripción: editar el campo 'description'.

Cada criterio tiene:
- id          : identificador corto en MAYÚSCULAS_CON_GUIONES, único.
                Es el valor que se persistirá en `resultados.criterio_fallido`.
- description : explicación que se inyecta en el prompt del auditor para
                que el modelo entienda cuándo aplicarlo.
"""

AUDIT_CRITERIA = [
    {
        "id": "FUERA_DE_CONTEXTO",
        "description": (
            "El comentario afirma hechos, definiciones o reglas de negocio "
            "que no están respaldados por ninguno de los insumos provistos "
            "(archivos del directorio input/ y tablas referenciadas en "
            "mapping.md / audit_mapping.md). Incluye casos de alucinación "
            "donde el modelo aporta información de su conocimiento previo."
        ),
    },
    {
        "id": "TERMINOLOGIA_INCORRECTA",
        "description": (
            "El comentario usa términos distintos a los términos canónicos "
            "definidos en los glosarios, diccionarios o taxonomías "
            "presentes en los insumos. Por ejemplo, dice 'cliente' cuando "
            "el glosario define 'titular', o usa sinónimos no estandarizados."
        ),
    },
    {
        "id": "GRANULARIDAD",
        "description": (
            "El comentario describe un nivel de granularidad incorrecto "
            "para el objeto al que pertenece. Ejemplos: un comentario de "
            "columna describe la tabla completa; un comentario de tabla "
            "describe únicamente el esquema; un comentario de esquema "
            "describe una sola tabla."
        ),
    },
    {
        "id": "IDIOMA_O_ESTILO",
        "description": (
            "El comentario no cumple los lineamientos editoriales: no está "
            "en español claro y de negocio, excede la longitud máxima "
            "esperada (500 chars para esquema/tabla, 300 para columna), "
            "incluye comillas, formato markdown, prefijos tipo 'Definición:' "
            "o cualquier metadato que no debería estar en el texto final."
        ),
    },
    {
        "id": "INFORMACION_FALTANTE",
        "description": (
            "El comentario omite información clave que sí está disponible "
            "en los insumos y debería incluirse: reglas de negocio "
            "asociadas a la columna, dominio de valores cuando es un "
            "catálogo/código/indicador, propósito principal cuando el "
            "insumo lo describe explícitamente."
        ),
    },
]


def get_criteria_ids() -> list:
    """Retorna la lista de IDs válidos de criterios."""
    return [c["id"] for c in AUDIT_CRITERIA]


def format_for_prompt() -> str:
    """Renderiza los criterios como bloque enumerado para el prompt."""
    lines = []
    for idx, c in enumerate(AUDIT_CRITERIA, start=1):
        lines.append(f"{idx}. {c['id']}: {c['description']}")
    return "\n".join(lines)
