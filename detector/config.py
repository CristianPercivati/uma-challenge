"""Configuración del servicio.

Los datos de GCP (endpoint, proyecto, credenciales) salen del entorno o de un archivo `.env` en la
carpeta desde donde se corre: ver `.env.example`. No hay valores reales en el código. Solo son
obligatorios cuando hay que llamar a la API: con `--extraccion` y la caché, el flujo corre sin ellos.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _env(nombre, defecto=""):
    return field(default_factory=lambda: os.getenv(nombre, defecto))


@dataclass
class Config:
    salida: Path = Path("salida")

    # Document AI (Enterprise Document OCR)
    endpoint: str = _env("DOCAI_ENDPOINT")
    field_mask: str = "text,pages.pageNumber,pages.dimension,pages.tokens"
    pages_per_request: int = 15     # tope del modo online del OCR
    max_retries: int = 3
    min_chars: int = 20             # menos caracteres de texto que esto = página escaneada

    # Gemini (flujo 3b)
    usar_llm: bool = False
    model: str = _env("GEMINI_MODEL", "gemini-3.8-flash")
    project_id: str = _env("GCP_PROJECT")
    location: str = _env("GCP_LOCATION", "global")

    # Precios en USD. Verificar los precios actuales antes de correr.
    price_ocr: float = 1.50 / 1000              # por página, DocumentAI
    price_in: float = 0.75                      # por 1M tokens
    price_out: float = 3.75                     # por 1M tokens

    # Reglas
    umbral_p: float = 0.05          # duplicado potencial si P(≥2 el mismo día) < 5 %
    umbral_p_llm: float = 0.30      # filtro laxo: candidatos que se le mandan al LLM

    def requerir(self, **valores):
        """Falla con un mensaje claro si falta alguna variable de entorno necesaria."""
        faltan = [nombre for nombre, valor in valores.items() if not valor]
        if faltan:
            raise RuntimeError(f"Falta configurar {', '.join(faltan)} en el entorno o en .env (ver .env.example). "
                               "Para correr sin GCP usá --extraccion.")

    @property
    def reglas_version(self):
        return f"reglas-v1 p<{self.umbral_p}"

    @property
    def cache(self):
        return self.salida / "cache"

    @property
    def db(self):
        return self.salida / "detector.sqlite"
