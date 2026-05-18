"""
Capa de base de datos para la aplicación web de búsqueda de PDF.

Almacenamiento basado en SQLite para fuentes, documentos y fragmentos de texto indexados.
"""
import sqlite3
from typing import Optional
import os
from datetime import datetime


class Database:
    def __init__(self, db_path: str = "pdf_search.db"):
        self.db_path = db_path
        self.init_db()

    # ─── Connection ────────────────────────────────────────────────────────────

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    # ─── Schema ────────────────────────────────────────────────────────────────

    def init_db(self):
        conn = self.get_connection()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sources (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                url         TEXT    UNIQUE NOT NULL,
                scraped     INTEGER DEFAULT 0,
                last_scraped TEXT,
                created_at  TEXT    DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS documents (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id     INTEGER,
                filename      TEXT NOT NULL,
                url           TEXT NOT NULL,
                year          INTEGER,
                pdf_path      TEXT,
                markdown_path TEXT,
                word_count    INTEGER DEFAULT 0,
                ocr_used      INTEGER DEFAULT 0,
                created_at    TEXT DEFAULT (datetime('now')),
                UNIQUE(url),
                FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS chunks (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                chunk_text  TEXT    NOT NULL,
                FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(document_id);
        """)
        conn.commit()
        conn.close()

    # ─── Sources ───────────────────────────────────────────────────────────────

    def add_source(self, url: str) -> bool:
        conn = self.get_connection()
        try:
            conn.execute("INSERT OR IGNORE INTO sources (url) VALUES (?)", (url,))
            conn.commit()
            return True
        except Exception as e:
            print(f"[DB] add_source error: {e}")
            return False
        finally:
            conn.close()

    def get_sources(self) -> list:
        conn = self.get_connection()
        rows = conn.execute(
            "SELECT * FROM sources ORDER BY created_at DESC"
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_source(self, source_id: int) -> Optional[dict]:
        conn = self.get_connection()
        row = conn.execute(
            "SELECT * FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def get_sources_with_documents(self) -> list:
        conn = self.get_connection()
        sources = conn.execute(
            "SELECT * FROM sources ORDER BY created_at DESC"
        ).fetchall()
        result = []
        for src in sources:
            s = dict(src)
            docs = conn.execute(
                "SELECT id, filename, url, year, word_count, ocr_used "
                "FROM documents WHERE source_id = ? ORDER BY created_at DESC",
                (src["id"],),
            ).fetchall()
            s["documents"] = [dict(d) for d in docs]
            result.append(s)
        conn.close()
        return result

    def delete_source(self, source_id: int):
        conn = self.get_connection()
        conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        conn.commit()
        conn.close()

    def mark_source_scraped(self, source_id: int):
        conn = self.get_connection()
        conn.execute(
            "UPDATE sources SET scraped=1, last_scraped=? WHERE id=?",
            (datetime.now().isoformat(sep=" ", timespec="seconds"), source_id),
        )
        conn.commit()
        conn.close()

    # ─── Documents ─────────────────────────────────────────────────────────────

    def document_exists(self, url: str) -> bool:
        conn = self.get_connection()
        row = conn.execute(
            "SELECT id FROM documents WHERE url=?", (url,)
        ).fetchone()
        conn.close()
        return row is not None

    def add_document(
        self,
        source_id: int,
        filename: str,
        url: str,
        year: int | None,
        pdf_path: str,
        markdown_path: str,
        word_count: int,
        ocr_used: bool = False,
    ) -> Optional[int]:
        conn = self.get_connection()
        try:
            cur = conn.execute(
                """INSERT OR IGNORE INTO documents
                   (source_id, filename, url, year, pdf_path, markdown_path, word_count, ocr_used)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (source_id, filename, url, year, pdf_path, markdown_path,
                 word_count, int(ocr_used)),
            )
            conn.commit()
            # If IGNORE fired, fetch existing id
            if cur.lastrowid == 0:
                row = conn.execute(
                    "SELECT id FROM documents WHERE url=?", (url,)
                ).fetchone()
                return row["id"] if row else None
            return cur.lastrowid
        except Exception as e:
            print(f"[DB] add_document error: {e}")
            return None
        finally:
            conn.close()

    # ─── Chunks ────────────────────────────────────────────────────────────────

    def add_chunks(self, document_id: int, chunks: list[str]):
        conn = self.get_connection()
        try:
            conn.execute(
                "DELETE FROM chunks WHERE document_id=?", (document_id,)
            )
            conn.executemany(
                "INSERT INTO chunks (document_id, chunk_text) VALUES (?,?)",
                [(document_id, c) for c in chunks if c.strip()],
            )
            conn.commit()
        finally:
            conn.close()

    def get_all_chunks_with_docs(self) -> list:
        conn = self.get_connection()
        rows = conn.execute(
            """SELECT c.chunk_text, d.url, d.filename, d.source_id
               FROM chunks c
               JOIN documents d ON c.document_id = d.id"""
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ─── Stats ─────────────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        conn = self.get_connection()
        total_docs  = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        total_words = conn.execute(
            "SELECT COALESCE(SUM(word_count),0) FROM documents"
        ).fetchone()[0]
        ocr_docs    = conn.execute(
            "SELECT COUNT(*) FROM documents WHERE ocr_used=1"
        ).fetchone()[0]
        docs_by_year = conn.execute(
            """SELECT year, COUNT(*) AS count
               FROM documents
               GROUP BY year
               ORDER BY year DESC"""
        ).fetchall()
        sources_total   = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        sources_scraped = conn.execute(
            "SELECT COUNT(*) FROM sources WHERE scraped=1"
        ).fetchone()[0]
        conn.close()
        return {
            "total_docs":      total_docs,
            "total_words":     total_words,
            "ocr_docs":        ocr_docs,
            "docs_by_year":    [dict(r) for r in docs_by_year],
            "sources_total":   sources_total,
            "sources_scraped": sources_scraped,
        }
