"""
Módulo de extracción de datos: obtiene enlaces PDF de páginas web, descarga los archivos PDF, los convierte a Markdown (con reconocimiento óptico de caracteres como alternativa para documentos escaneados o que solo contengan imágenes) y almacena todo en la base de datos.

"""
import os
import re
import threading
import time
from typing import Optional
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup

DOWNLOAD_DIR = "downloaded_pdfs"
MARKDOWN_DIR = "markdown_files"
CHUNK_WORDS  = 20 # num palabras por chunk para la búsqueda vectorial
MIN_TEXT_LEN = 100  # si el texto extraído es más corto que esto, intenta OCR

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; PDFSearchBot/1.0; "
        "+https://github.com/fdcirettg/desarrollo4_2026_1)"
    )
}


class Scraper:
    def __init__(self, db):
        self.db = db
        os.makedirs(DOWNLOAD_DIR, exist_ok=True)
        os.makedirs(MARKDOWN_DIR, exist_ok=True)


    def scrape(
        self,
        url: str,
        source_id: int,
        job_id: Optional[str] = None,
        jobs: Optional[dict] = None,
        lock=None,
    ) -> list:
        """
        Scrape *url* for PDF links, download + index each one.
        Progress is written to jobs[job_id] so the web UI can poll it.
        """
        html = self._fetch(url)
        if html is None:
            raise RuntimeError(f"Could not fetch: {url}")

        pdf_links = self._extract_pdf_links(html, url)
        scraped: list[dict] = []

        for link in pdf_links:
            if self.db.document_exists(link):
                continue

            filename = self._safe_filename(link)
            pdf_path = os.path.join(DOWNLOAD_DIR, filename)
            md_stem  = os.path.splitext(filename)[0]
            md_path  = os.path.join(MARKDOWN_DIR, f"{md_stem}.md")

            try:
                self._download(link, pdf_path)
                content, ocr_used = self._to_text(pdf_path, md_path)
                year       = self._extract_year(filename, link, content)
                word_count = len(content.split()) if content else 0

                doc_id = self.db.add_document(
                    source_id, filename, link, year,
                    pdf_path, md_path, word_count, ocr_used,
                )
                if doc_id:
                    chunks = self._make_chunks(content)
                    self.db.add_chunks(doc_id, chunks)

                entry = {
                    "filename": filename,
                    "url":      link,
                    "year":     year,
                    "words":    word_count,
                    "ocr":      ocr_used,
                }
                scraped.append(entry)

                if jobs is not None and job_id is not None:
                    _safe_update(jobs, job_id, {"docs": list(scraped)}, lock)

            except Exception as exc:
                print(f"[SCRAPER] Error processing {link}: {exc}")
                if jobs is not None and job_id is not None:
                    _safe_update(
                        jobs, job_id,
                        {"last_error": f"{filename}: {exc}"},
                        lock,
                    )

        self.db.mark_source_scraped(source_id)
        return scraped

    # ─── HTTP helpers ──────────────────────────────────────────────────────────

    def _fetch(self, url: str) -> Optional[str]:
        try:
            r = requests.get(url, headers=_HEADERS, timeout=15)
            r.raise_for_status()
            return r.text
        except Exception as exc:
            print(f"[SCRAPER] Fetch error {url}: {exc}")
            return None

    def _download(self, url: str, path: str):
        r = requests.get(url, headers=_HEADERS, timeout=30, stream=True)
        r.raise_for_status()
        with open(path, "wb") as fh:
            for chunk in r.iter_content(chunk_size=8192):
                fh.write(chunk)

    # ─── Link de extracción ───────────────────────────────────────────────────────

    def _extract_pdf_links(self, html: str, base_url: str) -> list[str]:
        soup  = BeautifulSoup(html, "html.parser")
        links = []
        for a in soup.find_all("a", href=True):
            href = a["href"].strip()
            if href.lower().endswith(".pdf"):
                absolute = urljoin(base_url, href)
                links.append(absolute)
        return list(dict.fromkeys(links))  # deduplicate, preserve order

    # ─── Extracción de texto ───────────────────────────────────────────────────────

    def _to_text(self, pdf_path: str, md_path: str) -> tuple[str, bool]:
        """Return (text, ocr_used).  Tries MarkItDown first, falls back to OCR."""
        content  = ""
        ocr_used = False

        # ── 1: MarkItDown ─────────────────────────────────────────────────
        try:
            from markitdown import MarkItDown
            converter = MarkItDown()
            result    = converter.convert(pdf_path)
            content   = (result.text_content or "").strip()
        except Exception as exc:
            print(f"[SCRAPER] MarkItDown failed ({pdf_path}): {exc}")

        # ── 2: OCR if text is too short ───────────────────────────────────
        if len(content) < MIN_TEXT_LEN:
            ocr_text = self._ocr(pdf_path)
            if ocr_text and len(ocr_text.strip()) > len(content):
                content  = ocr_text
                ocr_used = True

        if content:
            try:
                with open(md_path, "w", encoding="utf-8") as fh:
                    fh.write(content)
            except Exception as exc:
                print(f"[SCRAPER] Could not write markdown {md_path}: {exc}")

        return content, ocr_used

    def _ocr(self, pdf_path: str) -> str:
        """OCR a PDF using pdf2image + pytesseract."""
        try:
            from pdf2image import convert_from_path
            import pytesseract

            images = convert_from_path(pdf_path, dpi=300)
            parts  = []
            for img in images:
                text = pytesseract.image_to_string(img, lang="spa+eng")
                parts.append(text)
            return "\n".join(parts)
        except ImportError:
            print("[SCRAPER] OCR libs not available (pdf2image / pytesseract).")
            return ""
        except Exception as exc:
            print(f"[SCRAPER] OCR error ({pdf_path}): {exc}")
            return ""

    # ─── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _safe_filename(url: str) -> str:
        name = url.split("/")[-1].split("?")[0]
        # sanitise
        name = re.sub(r"[^\w.\-]", "_", name)
        return name or "document.pdf"

    @staticmethod
    def _extract_year(filename: str, url: str, content: str) -> Optional[int]:
        pattern = re.compile(r"\b(20[0-2]\d)\b")
        for text in (filename, url):
            m = pattern.search(text)
            if m:
                return int(m.group(1))
        # Busca los primeros 2000 caracteres del contenido extraído
        if content:
            matches = pattern.findall(content[:2000])
            if matches:
                return int(matches[0])
        return None

    @staticmethod
    def _make_chunks(content: str) -> list[str]:
        if not content:
            return []
        words  = content.split()
        chunks = []
        for i in range(0, len(words), CHUNK_WORDS):
            piece = " ".join(words[i : i + CHUNK_WORDS])
            if piece.strip():
                chunks.append(piece)
        return chunks


# ─── Thread-safe job dict helper ───────────────────────────────────────────────

def _safe_update(
    jobs: dict,
    job_id: str,
    updates: dict,
    lock=None,
):
    if lock:
        with lock:
            jobs[job_id].update(updates)
    else:
        jobs[job_id].update(updates)
