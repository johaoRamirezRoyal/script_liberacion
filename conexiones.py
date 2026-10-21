"""Conexiones externas: MySQL, Cloudinary y el endpoint de migración del backend."""

from contextlib import ExitStack
from pathlib import Path

import cloudinary
import cloudinary.api
import pymysql
import pymysql.cursors
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import Config

RUTA_ENDPOINT = "/api/migracion/cloudinary"


class ErrorFatalApi(Exception):
    """El endpoint rechaza el token o está apagado: no tiene sentido seguir."""


class ErrorLote(Exception):
    """Un lote falló completo (red, 5xx tras reintentos, 422, respuesta inválida)."""


# ---------- Base de datos ----------

def conectar_bd(cfg: Config) -> pymysql.connections.Connection:
    return pymysql.connect(
        host=cfg.db_host,
        port=cfg.db_port,
        user=cfg.db_username,
        password=cfg.db_password,
        database=cfg.db_database,
        charset="utf8mb4",
        connect_timeout=10,
        read_timeout=300,
    )


def columnas_existentes(conn: pymysql.connections.Connection) -> set[tuple[str, str]]:
    """(tabla, columna) de toda la BD, para saltar las fuentes que no existen."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT TABLE_NAME, COLUMN_NAME FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE()"
        )
        return {(t, c) for t, c in cur.fetchall()}


def leer_valores(conn: pymysql.connections.Connection, tabla: str, pk: str, columna: str):
    """Genera (id, valor) sin cargar toda la tabla en memoria (cursor sin buffer)."""
    sql = f"SELECT `{pk}`, `{columna}` FROM `{tabla}` WHERE `{columna}` IS NOT NULL AND `{columna}` <> ''"
    with conn.cursor(pymysql.cursors.SSCursor) as cur:
        cur.execute(sql)
        yield from cur


# ---------- Cloudinary ----------

def configurar_cloudinary(cfg: Config) -> None:
    cloudinary.config(
        cloud_name=cfg.cloudinary_cloud_name,
        api_key=cfg.cloudinary_api_key,
        api_secret=cfg.cloudinary_api_secret,
        secure=True,
    )


def ping_cloudinary() -> dict:
    return cloudinary.api.ping()


# ---------- Endpoint de migración ----------

class ClienteMigracion:
    def __init__(self, cfg: Config):
        self.url = cfg.api_url + RUTA_ENDPOINT
        self.sesion = requests.Session()
        self.sesion.headers.update({"X-Migracion-Token": cfg.migracion_api_token, "Accept": "application/json"})
        # Reintenta errores de red y 5xx con backoff (2s, 4s, 8s). POST no se reintenta por defecto.
        reintentos = Retry(
            total=3,
            backoff_factor=2,
            status_forcelist=(500, 502, 503, 504),
            allowed_methods=frozenset({"POST"}),
            raise_on_status=False,
        )
        self.sesion.mount("http://", HTTPAdapter(max_retries=reintentos))
        self.sesion.mount("https://", HTTPAdapter(max_retries=reintentos))

    def probar(self) -> str:
        """POST vacío: 422 (falta archivos) significa que el token pasó."""
        try:
            resp = self.sesion.post(self.url, timeout=(10, 30))
        except requests.RequestException as e:
            raise ErrorFatalApi(f"No se pudo conectar con {self.url}: {e}") from e
        self._verificar_fatal(resp)
        if resp.status_code != 422:
            raise ErrorFatalApi(f"Respuesta inesperada del endpoint: HTTP {resp.status_code} {resp.text[:200]}")
        return f"{self.url} responde y acepta el token"

    def subir_lote(self, directorio: str, archivos: list[Path]) -> list[dict]:
        """Sube un lote; devuelve un resultado por archivo, en el mismo orden."""
        with ExitStack() as pila:
            partes = [
                ("archivos[]", (ruta.name, pila.enter_context(ruta.open("rb")))) for ruta in archivos
            ]
            try:
                resp = self.sesion.post(
                    self.url, data={"directorio": directorio}, files=partes, timeout=(10, 600)
                )
            except requests.RequestException as e:
                raise ErrorLote(f"error de red: {e}") from e

        self._verificar_fatal(resp)
        if resp.status_code != 200:
            raise ErrorLote(f"HTTP {resp.status_code}: {self._mensaje(resp)}")

        try:
            datos = resp.json()["data"]
        except (ValueError, KeyError, TypeError) as e:
            raise ErrorLote(f"respuesta no es JSON válido: {resp.text[:200]}") from e
        if len(datos) != len(archivos):
            raise ErrorLote(f"el endpoint devolvió {len(datos)} resultados para {len(archivos)} archivos")
        return datos

    @staticmethod
    def _verificar_fatal(resp: requests.Response) -> None:
        if resp.status_code == 401:
            raise ErrorFatalApi("El endpoint rechazó el token (401). Revisa MIGRACION_API_TOKEN en ambos .env.")
        if resp.status_code == 404:
            raise ErrorFatalApi(
                "El endpoint no existe (404): falta MIGRACION_API_TOKEN en el .env del backend, "
                "o API_URL está mal."
            )

    @staticmethod
    def _mensaje(resp: requests.Response) -> str:
        try:
            cuerpo = resp.json()
            return str(cuerpo.get("errors") or cuerpo.get("message") or cuerpo)[:300]
        except ValueError:
            return resp.text[:300]
