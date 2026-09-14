"""Read research records of arbitrary size through Aurora Data API.

Both a growing audit catalog and a single long evidence excerpt can exceed the
transport limit. Page serialized records by character chunks, then reconstruct
them exactly. Callers own a repeatable-read transaction across all related reads.
"""

import json


CHUNK_CHARACTERS = 8192
CHUNKS_PER_PAGE = 8


def read_research_rows(conn, query, parameters=(), *, order_by):
    """Read a trusted SELECT with a deterministic, unique output-column order.

    query and order_by must be application SQL, never request parameters. Even
    with multibyte characters and JSON escaping, each chunk stays below 64 KB
    and each page below 1 MB. Original fields and JSON types are retained.
    """
    sql = f"""
        WITH records AS (
            SELECT row_number() OVER (ORDER BY {order_by}) AS row_no,
                   row_to_json(source)::text AS document
            FROM ({query}) AS source
        )
        SELECT row_no, chunk_no,
               substring(document FROM chunk_no * {CHUNK_CHARACTERS} + 1
                         FOR {CHUNK_CHARACTERS}) AS fragment
        FROM records
        CROSS JOIN LATERAL generate_series(
            0, (length(document) - 1) / {CHUNK_CHARACTERS}
        ) AS parts(chunk_no)
        WHERE (row_no, chunk_no) > (%s, %s)
        ORDER BY row_no, chunk_no LIMIT %s
    """
    cursor = (0, -1)
    result, fragments = [], []
    current_row = None
    while True:
        page = conn.execute(sql, (*parameters, *cursor, CHUNKS_PER_PAGE)).fetchall()
        for part in page:
            if current_row is not None and part["row_no"] != current_row:
                result.append(json.loads("".join(fragments)))
                fragments = []
            current_row = part["row_no"]
            fragments.append(part["fragment"])
        if len(page) < CHUNKS_PER_PAGE:
            if fragments:
                result.append(json.loads("".join(fragments)))
            return result
        cursor = (page[-1]["row_no"], page[-1]["chunk_no"])
