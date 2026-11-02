"""Base SQLite: filas extraídas, hallazgos, qué filas forman cada hallazgo y métricas por corrida."""
import sqlite3

from .metricas import sql_val

SCHEMA = """
CREATE TABLE IF NOT EXISTS filas (
    fila_id TEXT PRIMARY KEY, archivo TEXT, sha TEXT, pagina INT, renglon INT, orden INT,
    nro_int TEXT, fecha TEXT, servicio TEXT, codigo TEXT, descripcion TEXT, familia TEXT,
    cant INT, der TEXT, hon TEXT, total TEXT, x0 REAL, y0 REAL, x1 REAL, y1 REAL, conf_min REAL);
CREATE TABLE IF NOT EXISTS hallazgos (
    hallazgo_id TEXT PRIMARY KEY, tipo TEXT, regla_version TEXT, fecha TEXT, familia TEXT,
    codigos TEXT, veces INT, monto_en_riesgo TEXT, contiguas INT, nro_ints TEXT, ubicacion TEXT,
    motivo TEXT);
CREATE TABLE IF NOT EXISTS hallazgo_filas (
    hallazgo_id TEXT, fila_id TEXT, PRIMARY KEY (hallazgo_id, fila_id));
CREATE TABLE IF NOT EXISTS metricas (
    corrida TEXT, bloque TEXT, detalle TEXT, latencia_s REAL, paginas INT, tokens_in INT,
    tokens_out INT, costo_usd REAL, cache_hits INT);
"""
FILA_COLS = ["fila_id", "archivo", "sha", "pagina", "renglon", "orden", "nro_int", "dia", "servicio",
             "codigo", "descripcion", "familia", "cant", "der", "hon", "total",
             "x0", "y0", "x1", "y1", "conf_min"]
HALLAZGO_COLS = ["hallazgo_id", "tipo", "regla_version", "fecha", "familia", "codigos", "veces",
                 "monto_en_riesgo", "contiguas", "nro_ints", "ubicacion", "motivo"]


class Base:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(path)
        self.con.executescript(SCHEMA)

    def guardar_filas(self, df):
        """INSERT OR REPLACE: la misma extracción reescribe las mismas filas."""
        with self.con:
            self.con.executemany(f"INSERT OR REPLACE INTO filas VALUES ({', '.join('?' * len(FILA_COLS))})",
                                 [[sql_val(v) for v in row] for row in df[FILA_COLS].itertuples(index=False)])

    def guardar_frecuencias(self, freq):
        """Foto de la tabla de frecuencias usada en esta corrida."""
        freq.to_sql("frecuencias", self.con, if_exists="replace")

    def guardar_hallazgos(self, hallazgos):
        """INSERT OR IGNORE: un hallazgo que ya existe no se duplica ni se pisa."""
        with self.con:
            self.con.executemany(f"INSERT OR IGNORE INTO hallazgos ({', '.join(HALLAZGO_COLS)}) "
                                 f"VALUES ({', '.join('?' * len(HALLAZGO_COLS))})",
                                 [[sql_val(h[c]) for c in HALLAZGO_COLS] for h in hallazgos])
            self.con.executemany("INSERT OR IGNORE INTO hallazgo_filas VALUES (?, ?)",
                                 [(h["hallazgo_id"], f) for h in hallazgos for f in h["fila_ids"]])

    def guardar_metricas(self, corrida, met):
        cols = ["bloque", "detalle", "latencia_s", "paginas", "tokens_in", "tokens_out", "costo_usd", "cache_hits"]
        with self.con:
            self.con.executemany("INSERT INTO metricas VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                 [[corrida] + [sql_val(v) for v in r] for r in met[cols].itertuples(index=False)])

    def contar(self, tabla):
        return self.con.execute(f"SELECT COUNT(*) FROM {tabla}").fetchone()[0]
