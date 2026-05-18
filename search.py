"""
Módulo de búsqueda: búsqueda de texto completo con índice de Levenshtein sobre fragmentos de documentos indexados.

Estrategia
--------
Para cada fragmento almacenado, se desplaza una ventana deslizante con la misma longitud de palabra que la consulta.
Se desplaza a lo largo del fragmento para encontrar la *mejor* coincidencia local. Esto evita el problema de que una consulta corta obtenga una puntuación cercana a cero frente a un fragmento de 20 palabras.

El índice óptimo de la ventana se compara con el umbral del solicitante.

"""
import Levenshtein


def search_documents(
    db,
    query: str,
    threshold: float = 0.50,
    max_results: int = 60,
) -> list[dict]:
    """
    Return a list of result dicts, sorted by similarity descending.

    Each result:
        url         – original document URL
        filename    – PDF filename
        chunk       – the matching text block
        ratio       – 0-100 float  (percentage with 1 decimal, up to 3 digits)
    """
    if not query or not query.strip():
        return []

    query_clean  = query.strip().lower()
    query_words  = query_clean.split()
    q_len        = len(query_words)
    all_chunks   = db.get_all_chunks_with_docs()

    results: list[dict] = []

    for row in all_chunks:
        chunk_text  = row["chunk_text"]
        chunk_lower = chunk_text.lower()
        chunk_words = chunk_lower.split()
        c_len       = len(chunk_words)

        best_ratio = 0.0
        best_window = chunk_text 

        if c_len == 0:
            continue

        # ── Ventana de longitud de consulta ─────────────────────────────────────
        window_len = min(q_len, c_len)
        for i in range(c_len - window_len + 1):
            window = " ".join(chunk_words[i : i + window_len])
            ratio  = Levenshtein.ratio(window, query_clean)
            if ratio > best_ratio:
                best_ratio  = ratio
                # Recover original-case window from chunk_text
                orig_words   = chunk_text.split()
                best_window  = " ".join(orig_words[i : i + window_len])

        # ── Pruebe también con chunk completo ─────────────────────────────────────────────────
        full_ratio = Levenshtein.ratio(chunk_lower, query_clean)
        if full_ratio > best_ratio:
            best_ratio  = full_ratio
            best_window = chunk_text

        if best_ratio >= threshold:
            results.append(
                {
                    "url":      row["url"],
                    "filename": row["filename"],
                    "chunk":    best_window,
                    "ratio":    round(best_ratio * 100, 1),  # 0–100, up to 3 digits
                }
            )

    # Filtra resultados por similitud
    results.sort(key=lambda x: x["ratio"], reverse=True)

    # Deduplica por URL, manteniendo solo los 3 mejores resultados por URL
    seen: dict[str, int] = {}
    deduped: list[dict]  = []
    for r in results:
        count = seen.get(r["url"], 0)
        if count < 3:
            deduped.append(r)
            seen[r["url"]] = count + 1

    return deduped[:max_results]
