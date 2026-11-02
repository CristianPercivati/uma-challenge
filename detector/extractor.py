"""1. Extractor: splitter (texto o imagen) y procesadores (parser interno · Document AI con caché)."""
import base64
import hashlib
import json
import logging
import random
import time
from pathlib import Path

import pandas as pd
import pymupdf

from .metricas import sha
from .parser import FIELDS, parse_page

log = logging.getLogger(__name__)

# Bordes típicos de la tabla, para las extracciones viejas que no guardaron x0/x1.
X0_TABLA, X1_TABLA = 0.045, 0.978


def dividir(paths, cfg, met):
    """1a. Splitter: clasifica cada página en 'texto' o 'imagen'. Devuelve el lote."""
    lote = {}
    with met.medir("1a splitter") as m:
        for path in map(Path, paths):
            with pymupdf.open(path) as doc:
                rutas = {i: "texto" if len(p.get_text().strip()) >= cfg.min_chars else "imagen"
                         for i, p in enumerate(doc)}
            lote[path.name] = {"path": path, "archivo": path.name, "rutas": rutas,
                               "sha": hashlib.sha256(path.read_bytes()).hexdigest()}
            m["paginas"] += len(rutas)
    return lote


class DocumentAI:
    """Cliente online de Document AI. La sesión se crea solo si hay que llamar a la API."""

    def __init__(self, cfg):
        self.cfg = cfg
        self._session = None

    def session(self):
        if self._session is None:
            self.cfg.requerir(DOCAI_ENDPOINT=self.cfg.endpoint)
            import google.auth
            from google.auth.transport.requests import AuthorizedSession
            creds = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])[0]
            self._session = AuthorizedSession(creds)
        return self._session

    def procesar(self, pdf_bytes):
        """Un request; reintenta 429/5xx con espera creciente."""
        body = {"rawDocument": {"content": base64.b64encode(pdf_bytes).decode(),
                                "mimeType": "application/pdf"},
                "fieldMask": self.cfg.field_mask}
        for attempt in range(self.cfg.max_retries + 1):
            resp = self.session().post(self.cfg.endpoint, json=body, timeout=300)
            if resp.ok:
                return resp.json()["document"]
            if resp.status_code not in (429, 500, 502, 503, 504) or attempt == self.cfg.max_retries:
                raise RuntimeError(f"Document AI {resp.status_code}: {resp.text[:300]}")
            time.sleep(2 ** attempt + random.random())


def pdf_pages(path, pages):
    """PDF nuevo con solo esas páginas (0-based)."""
    with pymupdf.open(path) as src, pymupdf.open() as out:
        for i in pages:
            out.insert_pdf(src, from_page=i, to_page=i)
        return out.tobytes()


def procesar_texto(lote):
    """Páginas con capa de texto -> parser interno (pendiente: en este lote no hay)."""
    pages = [i for i, r in lote["rutas"].items() if r == "texto"]
    if pages:
        log.warning("%s: %d páginas con texto sin procesar (parser interno pendiente)", lote["archivo"], len(pages))
    return []


def procesar_imagenes(lote, cfg, docai, m):
    """Páginas imagen -> Document AI en tramos. Caché por (sha del PDF, páginas, endpoint, field mask)."""
    pages = [i for i, r in lote["rutas"].items() if r == "imagen"]
    filas = []
    for k in range(0, len(pages), cfg.pages_per_request):
        chunk = pages[k:k + cfg.pages_per_request]
        f = cfg.cache / f"docai_{sha(lote['sha'], chunk, cfg.endpoint, cfg.field_mask)}.json"
        if f.exists():
            doc = json.loads(f.read_text(encoding="utf-8"))
            m["cache_hits"] += 1
        else:
            doc = docai.procesar(pdf_pages(lote["path"], chunk))
            f.write_text(json.dumps(doc), encoding="utf-8")
            m["costo_usd"] += len(chunk) * cfg.price_ocr
        m["paginas"] += len(chunk)
        for p in doc.get("pages", []):
            p["pageNumber"] = chunk[p["pageNumber"] - 1] + 1   # número de página en el PDF original
            filas += parse_page(doc["text"], p)
    return filas


def extraer(lote, cfg, met):
    """1b. Extracción del lote completo. Devuelve los renglones crudos (texto tal cual se leyó)."""
    cfg.cache.mkdir(parents=True, exist_ok=True)
    docai = DocumentAI(cfg)
    crudo = []
    for x in lote.values():
        with met.medir("1b extracción", x["archivo"]) as m:
            filas = procesar_texto(x) + procesar_imagenes(x, cfg, docai, m)
            for f in filas:
                f["archivo"], f["sha"] = x["archivo"], x["sha"]
            crudo += filas
    return crudo


def cargar_extraccion(csvs, lote, met):
    """1b alternativo: renglones de una extracción ya hecha (sin OCR ni credenciales de GCP).

    Acepta el `extraccion.csv` que escribe este servicio y los CSV del notebook de exploración
    (importes con punto decimal y sin x0/x1).
    """
    crudo = []
    with met.medir("1b extracción", "desde CSV") as m:
        for csv in csvs:
            t = pd.read_csv(csv, dtype=str, keep_default_na=False)
            legado = "x0" not in t.columns
            for r in t.to_dict("records"):
                x = lote.get(r["archivo"])
                if x is None:
                    continue
                f = {c: r.get(c, "") for c in FIELDS}
                if legado:
                    for c in ("der", "hon", "total"):
                        f[c] = f[c].replace(".", ",")
                f.update(pagina=int(r["pagina"]), x0=float(r.get("x0") or X0_TABLA),
                         x1=float(r.get("x1") or X1_TABLA), y0=float(r["y0"]), y1=float(r["y1"]),
                         conf_min=float(r.get("conf_min") or 0), archivo=x["archivo"], sha=x["sha"])
                crudo.append(f)
        m["paginas"] = sum(len(x["rutas"]) for x in lote.values())
    sin_filas = set(lote) - {f["archivo"] for f in crudo}
    if sin_filas:
        log.warning("Sin renglones en la extracción para: %s", ", ".join(sorted(sin_filas)))
    return crudo
