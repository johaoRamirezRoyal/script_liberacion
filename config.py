"""Carga y valida las variables de entorno del script (.env junto a este archivo)."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DIRECTORIO_SCRIPT = Path(__file__).resolve().parent

VARIABLES_REQUERIDAS = (
    "DB_HOST",
    "DB_PORT",
    "DB_DATABASE",
    "DB_USERNAME",
    "DB_PASSWORD",
    "CLOUDINARY_CLOUD_NAME",
    "CLOUDINARY_API_KEY",
    "CLOUDINARY_API_SECRET",
    "API_URL",
    "MIGRACION_API_TOKEN",
    "UPLOAD_ROOT",
)


class ErrorConfiguracion(Exception):
    """Falta una variable o tiene un valor inválido."""


@dataclass(frozen=True)
class Config:
    db_host: str
    db_port: int
    db_database: str
    db_username: str
    db_password: str
    cloudinary_cloud_name: str
    cloudinary_api_key: str
    cloudinary_api_secret: str
    api_url: str
    migracion_api_token: str
    upload_root: Path


def cargar() -> Config:
    load_dotenv(DIRECTORIO_SCRIPT / ".env")

    # DB_PASSWORD puede ser vacía en local; el resto no.
    faltantes = [
        nombre
        for nombre in VARIABLES_REQUERIDAS
        if os.getenv(nombre) is None or (nombre != "DB_PASSWORD" and not os.getenv(nombre, "").strip())
    ]
    if faltantes:
        raise ErrorConfiguracion(
            "Faltan variables de entorno: " + ", ".join(faltantes) + ". Revisa el .env (ver .env.example)."
        )

    try:
        db_port = int(os.environ["DB_PORT"])
    except ValueError as e:
        raise ErrorConfiguracion(f"DB_PORT debe ser un número, llegó '{os.environ['DB_PORT']}'.") from e

    return Config(
        db_host=os.environ["DB_HOST"],
        db_port=db_port,
        db_database=os.environ["DB_DATABASE"],
        db_username=os.environ["DB_USERNAME"],
        db_password=os.environ["DB_PASSWORD"],
        cloudinary_cloud_name=os.environ["CLOUDINARY_CLOUD_NAME"],
        cloudinary_api_key=os.environ["CLOUDINARY_API_KEY"],
        cloudinary_api_secret=os.environ["CLOUDINARY_API_SECRET"],
        api_url=os.environ["API_URL"].rstrip("/"),
        migracion_api_token=os.environ["MIGRACION_API_TOKEN"],
        upload_root=Path(os.environ["UPLOAD_ROOT"]),
    )
