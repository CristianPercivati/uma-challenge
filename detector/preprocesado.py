"""2. Pre-procesado: limpieza, normalización a familias y tabla de frecuencias."""
import math
import re

import pandas as pd

from .parser import FIELDS, ar_decimal

FAMILIAS = {   # mismo estudio, códigos distintos. El resto: cada código es su propia familia.
    "180161": "ECOGRAFIA TRANSVAGINAL", "861219": "ECOGRAFIA TRANSVAGINAL",
    "180106": "ECOGRAFIA MAMARIA",      "861220": "ECOGRAFIA MAMARIA",
    "341018": "UROTOMOGRAFIA",          "341177": "UROTOMOGRAFIA",
    "341012": "TAC OTROS ORGANOS",      "3410121": "TAC OTROS ORGANOS",
    "3410123": "TAC OTROS ORGANOS",     "3410126": "TAC OTROS ORGANOS",
}
CODIGO = re.compile(r"^\d{4,7}(?:X1)?\s")
REPETIBLES = re.compile(r"\bCADA\b|SUBSIGUIENTE|SUBISGUIENTE|SEGUND|POR REGIO", re.I)


def limpiar(crudo, met):
    """Renglones crudos -> filas limpias. Devuelve (df, totales impresos por archivo, resumen)."""
    with met.medir("2 pre-procesado") as m:
        df = pd.DataFrame(crudo)
        for c in FIELDS:
            df[c] = df[c].fillna("") if c in df else ""

        # "Apellido y Nombre" está tapado: si trae un código, es la prestación corrida de columna.
        corrida = (df["prestacion"] == "") & df["paciente"].str.match(CODIGO)
        df.loc[corrida, "prestacion"] = df.loc[corrida, "paciente"]
        df.loc[corrida, "paciente"] = ""

        # Fila de TOTAL impreso (sin Nro. Int., con importe) y ruido (sin Nro. Int. ni importe).
        sin_id = df["nro_int"] == ""
        es_total = sin_id & (df["total"] != "")
        totales = {a: ar_decimal(g["total"].iloc[-1]) for a, g in df[es_total].groupby("archivo")}
        ruido = int((sin_id & ~es_total).sum())
        df = df[~sin_id].copy()

        df[["codigo", "descripcion"]] = df["prestacion"].str.extract(r"^(\S+)\s*(.*)$").fillna("")
        for c in ["der", "hon", "total"]:
            df[c] = df[c].map(ar_decimal)
        df["cant"] = pd.to_numeric(df["cant"], errors="coerce").astype("Int64")
        df["dia"] = (pd.to_datetime(df["fecha"], format="%d/%m/%Y", errors="coerce")
                     .dt.strftime("%Y-%m-%d").fillna(""))

        # Familia: agrupa códigos equivalentes. Práctica: el nombre legible (descripción más frecuente).
        df["familia"] = df["codigo"].map(FAMILIAS).fillna(df["codigo"])
        canon = df.groupby("codigo")["descripcion"].agg(lambda s: s.mode().iat[0])
        df["practica"] = df["codigo"].map(FAMILIAS).fillna(df["codigo"].map(canon))

        # Ids estables: mismo PDF y misma extracción -> mismo id. `orden` sirve para ver filas contiguas.
        df = df.sort_values(["archivo", "pagina", "y0"]).reset_index(drop=True)
        df["renglon"] = df.groupby(["archivo", "pagina"]).cumcount() + 1
        df["orden"] = df.groupby("archivo").cumcount()
        df["fila_id"] = [f"{s[:8]}-p{p:02d}-r{r:02d}" for s, p, r in zip(df["sha"], df["pagina"], df["renglon"])]
        m["paginas"] = int(df.groupby("archivo")["pagina"].nunique().sum())

    resumen = {"filas": len(df), "prestaciones_reparadas": int(corrida.sum()), "renglones_ruido": ruido,
               "archivos_con_total": len(totales)}
    return df, totales, resumen


def frecuencias(df, cfg):
    """¿Qué tan raro es ver la misma práctica 2+ veces el mismo día?

    λ = renglones / días con actividad en el lote, y con Poisson P(≥2) = 1 − e^(−λ)(1 + λ).
    Se divide por los días del lote (no por los días en que aparece la práctica) para que los
    propios duplicados no inflen la frecuencia.
    """
    dias_lote = df.loc[df["dia"] != "", "dia"].nunique()
    freq = (df[df["familia"] != ""].groupby("familia")
            .agg(practica=("practica", "first"), renglones=("fila_id", "size"),
                 dias_con_practica=("dia", "nunique")))
    freq["lambda"] = (freq["renglones"] / dias_lote).round(3)
    freq["p_2"] = freq["lambda"].map(lambda l: round(1 - math.exp(-l) * (1 + l), 4))
    freq["repetible"] = freq.index.str.endswith("X1") | freq["practica"].str.contains(REPETIBLES)
    freq["candidata"] = ~freq["repetible"] & (freq["p_2"] < cfg.umbral_p)
    return freq.sort_values("lambda", ascending=False), dias_lote
