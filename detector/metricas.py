"""Métricas de producción por bloque y utilidades compartidas."""
import hashlib
import math
import time
from contextlib import contextmanager
from decimal import Decimal

import pandas as pd

COLUMNAS = ["latencia_s", "paginas", "tokens_in", "tokens_out", "costo_usd", "cache_hits"]


class Metricas:
    """Latencia, páginas, tokens, costo y aciertos de caché de cada bloque."""

    def __init__(self):
        self.filas = {}   # (bloque, detalle) -> fila

    @contextmanager
    def medir(self, bloque, detalle=""):
        m = {"bloque": bloque, "detalle": detalle, "latencia_s": 0.0, "paginas": 0,
             "tokens_in": 0, "tokens_out": 0, "costo_usd": 0.0, "cache_hits": 0}
        t0 = time.perf_counter()
        try:
            yield m
        finally:
            m["latencia_s"] = round(time.perf_counter() - t0, 3)
            self.filas[(bloque, detalle)] = m

    def tabla(self, paginas_lote):
        """Una fila por bloque más el TOTAL. Las páginas del TOTAL son las del lote, no la suma."""
        met = pd.DataFrame(self.filas.values())
        total = met[["latencia_s", "tokens_in", "tokens_out", "costo_usd", "cache_hits"]].sum()
        total["paginas"] = paginas_lote
        met = pd.concat([met, pd.DataFrame([{"bloque": "TOTAL", "detalle": "", **total}])], ignore_index=True)
        met = met.astype({c: int for c in ["paginas", "tokens_in", "tokens_out", "cache_hits"]})
        met["costo_usd"] = met["costo_usd"].round(5)
        met["latencia_s"] = met["latencia_s"].round(3)
        return met


def sha(*parts, n=16):
    """Hash corto y estable: base de la caché y de los ids."""
    return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:n]


def sql_val(v):
    """Decimal -> texto (sin redondeos de float); tipos de numpy/pandas -> Python."""
    if v is None or v is pd.NA or (isinstance(v, float) and math.isnan(v)):
        return None
    if isinstance(v, Decimal):
        return str(v)
    return v.item() if hasattr(v, "item") else v
