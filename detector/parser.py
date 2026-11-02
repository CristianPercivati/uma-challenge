"""Parser de la planilla "Rendición de Atención Ambulatoria": tokens de Document AI -> una fila por renglón."""
import logging
import re
import unicodedata
from bisect import bisect_right
from decimal import Decimal, InvalidOperation
from statistics import median

log = logging.getLogger(__name__)

ROW_TOL = 0.6       # misma fila si los centros difieren menos que ROW_TOL × alto de token

# Títulos de izquierda a derecha.
# "left": el contenido arranca donde arranca el título (texto).
# "right": el contenido termina donde termina el título (números).
HEADERS = [
    ("nro_int",      "Nro. Int.",           "left"),
    ("nro_afiliado", "Nro. de Afiliado",    "left"),
    ("nro_bono",     "Nro. de Bono",        "left"),
    ("profesional",  "Profesional Efector", "left"),
    ("servicio",     "Servicio",            "left"),
    ("fecha",        "Fecha",               "left"),
    ("paciente",     "Apellido y Nombre",   "left"),
    ("prestacion",   "Prestación",          "left"),
    ("cant",         "Cant.",               "right"),
    ("der",          "Der.",                "right"),
    ("hon",          "Hon.",                "right"),
    ("total",        "Total",               "right"),
]
FIELDS = [n for n, _, _ in HEADERS]

# El OCR a veces devuelve letras griegas iguales a las latinas ("ΜΑPA").
GREEK = str.maketrans("ΑΒΕΖΗΙΚΜΝΟΡΤΥΧοα", "ABEZHIKMNOPTYXoa")


def key(s):
    """Versión comparable: sin acentos, minúsculas, solo letras y números."""
    s = unicodedata.normalize("NFKD", s.translate(GREEK))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]", "", s)


def anchor_text(full, layout):
    """Recorta del texto completo el tramo al que apunta el elemento."""
    segs = (layout.get("textAnchor") or {}).get("textSegments") or []
    return "".join(full[int(s.get("startIndex", 0)):int(s.get("endIndex", 0))] for s in segs)


def page_tokens(full, page):
    """Tokens de la página con su texto y su caja normalizada 0..1."""
    out = []
    for t in page.get("tokens", []):
        lay = t.get("layout", {})
        if lay.get("orientation", 0) not in (0, 1, "PAGE_UP", "ORIENTATION_UNSPECIFIED"):
            continue  # texto rotado (logos, sellos), no es la tabla
        v = (lay.get("boundingPoly") or {}).get("normalizedVertices") or []
        if not v:
            continue
        xs = [p.get("x", 0.0) for p in v]
        ys = [p.get("y", 0.0) for p in v]
        raw = anchor_text(full, lay).replace("\n", " ").translate(GREEK)
        if t.get("detectedBreak") and not raw.endswith(" "):
            raw += " "
        out.append({
            "raw": raw, "key": key(raw), "conf": lay.get("confidence", 0.0),
            "x0": min(xs), "x1": max(xs), "y0": min(ys), "y1": max(ys),
            "xc": (min(xs) + max(xs)) / 2, "yc": (min(ys) + max(ys)) / 2,
        })
    return out


def group_rows(toks):
    """Agrupa tokens en filas visuales según su altura en la página."""
    if not toks:
        return []
    h = median(t["y1"] - t["y0"] for t in toks)
    rows = []
    for t in sorted(toks, key=lambda t: t["yc"]):
        if rows and abs(t["yc"] - rows[-1]["yc"]) <= ROW_TOL * h:
            rows[-1]["toks"].append(t)
            rows[-1]["yc"] = median(x["yc"] for x in rows[-1]["toks"])
        else:
            rows.append({"yc": t["yc"], "toks": [t]})
    for r in rows:
        r["toks"].sort(key=lambda t: t["x0"])
    return rows


def match_headers(toks):
    """Busca los títulos, en orden, dentro de una fila. Devuelve {columna: caja del título}."""
    found, i = {}, 0
    for name, label, align in HEADERS:
        target = key(label)
        for j in range(i, len(toks)):
            if not toks[j]["key"]:
                continue
            acc, k = "", j
            while k < len(toks) and len(acc) < len(target) and target.startswith(acc + toks[k]["key"]):
                acc += toks[k]["key"]
                k += 1
            if acc == target:
                span = [t for t in toks[j:k] if t["key"]]
                found[name] = {"align": align,
                               "x0": min(t["x0"] for t in span), "x1": max(t["x1"] for t in span)}
                i = k
                break
    return found


def column_cuts(found):
    """Límites horizontales entre columnas, según cómo se alinea cada una."""
    cols = sorted(found.items(), key=lambda kv: kv[1]["x0"])
    cuts = []
    for (_, a), (_, b) in zip(cols, cols[1:]):
        if a["align"] == "right" and b["align"] == "right":
            cuts.append(a["x1"])                    # entre números: donde termina el título izquierdo
        elif a["align"] == "right":
            cuts.append((a["x1"] + b["x0"]) / 2)    # número y después texto: a mitad de camino
        else:
            cuts.append(b["x0"])                    # después de texto: donde arranca el título siguiente
    return [n for n, _ in cols], cuts


def clean(s):
    """Saca el ruido de los tachados ('|||', '....') y espacios de más."""
    s = re.sub(r"\s+", " ", s.replace("|", " ")).strip()
    return s.strip(" .")


def parse_page(full, page):
    """Renglones de una página, con su caja (x0, x1, y0, y1) y la confianza mínima del OCR."""
    n_page = page.get("pageNumber")
    rows = group_rows(page_tokens(full, page))
    header = max(rows, key=lambda r: len(match_headers(r["toks"])), default=None)
    found = match_headers(header["toks"]) if header else {}
    if len(found) < len(HEADERS) // 2:
        log.warning("Página %s: no encontré la fila de títulos", n_page)
        return []
    names, cuts = column_cuts(found)
    top = max(t["y1"] for t in header["toks"])
    out = []
    for r in rows:
        if r["yc"] <= top:
            continue  # encabezado de la página y fila de títulos
        if re.search(r"pagina\d+de\d+", "".join(t["key"] for t in r["toks"])):
            continue  # pie "Página X de N"
        cells = dict.fromkeys(names, "")
        for t in r["toks"]:
            cells[names[bisect_right(cuts, t["xc"])]] += t["raw"]
        cells = {n: clean(v) for n, v in cells.items()}
        if not any(cells.values()):
            continue
        cells.update(
            pagina=n_page,
            x0=round(min(t["x0"] for t in r["toks"]), 4),
            x1=round(max(t["x1"] for t in r["toks"]), 4),
            y0=round(min(t["y0"] for t in r["toks"]), 4),
            y1=round(max(t["y1"] for t in r["toks"]), 4),
            conf_min=round(min(t["conf"] for t in r["toks"]), 3),
        )
        out.append(cells)
    return out


def ar_decimal(s):
    """'144283,00' o '1.234,56' -> Decimal; vacío o ilegible -> None."""
    s = (s or "").replace("$", "").replace(" ", "")
    try:
        return Decimal(s.replace(".", "").replace(",", ".")) if s else None
    except InvalidOperation:
        return None
