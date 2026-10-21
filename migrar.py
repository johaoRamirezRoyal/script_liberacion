"""CLI: migra a Cloudinary los archivos del disco de uploads referenciados en la BD.

    python migrar.py --probar-conexiones
    python migrar.py [--tablas firmas,recaudo] [--lote 5] [--max-mb-lote 8] [--limite 50]
    python migrar.py --borrar-subidos salida/<fecha>/subidos.csv [--confirmar]

Salidas en salida/<fecha>/: migracion.log (una línea por archivo), resultados.csv
(mapeo fila → URL de Cloudinary), subidos.csv (archivos únicos ya en Cloudinary) y
reporte.txt (% de éxito por tabla.columna).
"""

import argparse
import csv
import logging
import os
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

import pymysql

import config
from conexiones import (
    ClienteMigracion,
    ErrorFatalApi,
    ErrorLote,
    columnas_existentes,
    conectar_bd,
    configurar_cloudinary,
    existe_en_url,
    leer_valores,
    ping_cloudinary,
)
from fuentes import FUENTES, Fuente

MAX_BYTES_ARCHIVO = 10 * 1024 * 1024  # límite del endpoint (y de Cloudinary en el plan gratuito)

SUBIDO, FALLIDO, OMITIDO = "subido", "fallido", "omitido"

log = logging.getLogger("migracion")


@dataclass
class Registro:
    """Una fila de la BD que apunta a un archivo, y qué pasó con él."""

    fuente: Fuente
    id: object
    valor: str
    ruta_rel: str = ""
    estado: str = ""
    mensaje: str = ""
    public_id: str = ""
    url: str = ""


# ---------- Disco ----------

def indexar_disco(raiz: Path) -> dict[str, list[str]]:
    """nombre de archivo → rutas relativas (posix) donde aparece. La BD guarda casi
    siempre el nombre suelto y la subcarpeta depende del módulo, así que se busca por nombre."""
    indice: dict[str, list[str]] = defaultdict(list)
    for carpeta, _, archivos in os.walk(raiz):
        for nombre in archivos:
            indice[nombre].append(Path(carpeta, nombre).relative_to(raiz).as_posix())
    for rutas in indice.values():
        rutas.sort(key=lambda r: (r.count("/"), r))  # la raíz primero
    return indice


def resolver_ruta(valor: str, raiz: Path, indice: dict[str, list[str]]) -> tuple[str | None, str]:
    """Devuelve (ruta relativa, aviso). None si no está en disco."""
    valor = valor.strip().replace("\\", "/").lstrip("/")
    if "/" in valor:
        ruta = PurePosixPath(valor)
        if ".." in ruta.parts:
            return None, "ruta con '..', se ignora"
        if (raiz / valor).is_file():
            return valor, ""
    rutas = indice.get(PurePosixPath(valor).name, [])
    if not rutas:
        return None, ""
    aviso = f"el nombre aparece en {len(rutas)} carpetas, se usa {rutas[0]}" if len(rutas) > 1 else ""
    return rutas[0], aviso


# ---------- Recolección ----------

def recolectar(conn, cfg: config.Config, fuentes: list[Fuente], indice, registros: list[Registro]) -> dict[str, list[Registro]]:
    """Lee todas las fuentes, clasifica cada valor y lo agrega a `registros` (lista del
    llamador, para que un Ctrl+C a mitad conserve lo leído). Devuelve los pendientes de subir
    agrupados por ruta: un archivo se sube una vez aunque lo usen varias filas."""
    existentes = columnas_existentes(conn)
    pendientes: dict[str, list[Registro]] = defaultdict(list)

    for fuente in fuentes:
        if (fuente.tabla, fuente.columna) not in existentes or (fuente.tabla, fuente.pk) not in existentes:
            log.warning("[SALTADA] %s no existe en la BD", fuente.clave)
            continue

        cantidad = 0
        for id_fila, valor in leer_valores(conn, fuente.tabla, fuente.pk, fuente.columna):
            cantidad += 1
            reg = Registro(fuente=fuente, id=id_fila, valor=str(valor))
            registros.append(reg)
            limpio = reg.valor.strip()

            if limpio.lower().startswith(("http://", "https://")):
                terminar(reg, OMITIDO, "ya es una URL externa")
                continue
            if not PurePosixPath(limpio).suffix:
                terminar(reg, OMITIDO, "no es un archivo (sin extensión)")
                continue

            # Un valor basura (p. ej. el binario de una imagen guardado como texto) o un archivo
            # sin permisos falla solo esa fila, no toda la corrida.
            try:
                ruta, aviso = resolver_ruta(limpio, cfg.upload_root, indice)
                tamano = (cfg.upload_root / ruta).stat().st_size if ruta else 0
            except OSError as e:
                terminar(reg, FALLIDO, f"valor no válido como archivo ({e.strerror})")
                continue
            if ruta is None:
                terminar(reg, FALLIDO, aviso or "no existe en disco")
                continue
            if aviso:
                log.warning("[AVISO] %s id=%s %s: %s", fuente.clave, id_fila, limpio, aviso)

            reg.ruta_rel = ruta
            if tamano > MAX_BYTES_ARCHIVO:
                terminar(reg, FALLIDO, f"excede 10 MB ({tamano / 1048576:.1f} MB)")
                continue
            pendientes[ruta].append(reg)

        log.info("%s: %d filas con valor", fuente.clave, cantidad)

    return pendientes


# ---------- Subida ----------

def armar_lotes(rutas: list[str], raiz: Path, max_archivos: int, max_bytes: int):
    """Agrupa por directorio (el endpoint recibe uno por petición) y corta por cantidad y peso."""
    por_directorio: dict[str, list[str]] = defaultdict(list)
    for ruta in rutas:
        directorio = str(PurePosixPath(ruta).parent)
        por_directorio["" if directorio == "." else directorio].append(ruta)

    for directorio, rutas_dir in sorted(por_directorio.items()):
        lote, peso = [], 0
        for ruta in rutas_dir:
            tamano = (raiz / ruta).stat().st_size
            if lote and (len(lote) >= max_archivos or peso + tamano > max_bytes):
                yield directorio, lote
                lote, peso = [], 0
            lote.append(ruta)
            peso += tamano
        if lote:
            yield directorio, lote


def subir(cliente: ClienteMigracion, cfg: config.Config, pendientes: dict[str, list[Registro]], args) -> None:
    # Con --limite, el resto queda sin estado: sale como "pendiente" en el reporte.
    rutas = sorted(pendientes)[: args.limite]

    total, hechos = len(rutas), 0
    log.info("Archivos únicos a subir: %d", total)

    for directorio, lote in armar_lotes(rutas, cfg.upload_root, args.lote, args.max_mb_lote * 1024 * 1024):
        try:
            resultados = cliente.subir_lote(directorio, [cfg.upload_root / r for r in lote])
        except ErrorLote as e:
            resultados = [{"ok": False, "mensaje": f"lote fallido: {e}"} for _ in lote]

        for ruta, res in zip(lote, resultados):
            for reg in pendientes[ruta]:
                reg.public_id = res.get("public_id") or ""
                reg.url = res.get("url") or ""
                terminar(reg, SUBIDO if res.get("ok") else FALLIDO, res.get("mensaje") or "")

        hechos += len(lote)
        log.info("Progreso: %d/%d archivos (%.1f%%)", hechos, total, hechos * 100 / total)


def terminar(reg: Registro, estado: str, mensaje: str) -> None:
    reg.estado, reg.mensaje = estado, mensaje
    etiqueta = {SUBIDO: "[OK]", FALLIDO: "[ERROR]", OMITIDO: "[OMITIDO]"}[estado]
    detalle = f"→ {reg.url}" if estado == SUBIDO else f"— {mensaje}"
    nivel = logging.ERROR if estado == FALLIDO else logging.INFO
    valor = reg.valor if len(reg.valor) <= 150 else reg.valor[:150] + "…"
    log.log(nivel, "%s %s id=%s %s %s", etiqueta, reg.fuente.clave, reg.id, valor, detalle)


# ---------- Salidas ----------

def escribir_csv(registros: list[Registro], ruta: Path) -> None:
    with ruta.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["tabla", "columna", "id", "valor_original", "ruta_local", "estado", "public_id", "url", "mensaje"])
        for r in registros:
            w.writerow([r.fuente.tabla, r.fuente.columna, r.id, r.valor, r.ruta_rel, r.estado or "sin procesar",
                        r.public_id, r.url, r.mensaje])


def escribir_subidos(registros: list[Registro], ruta: Path) -> None:
    """Un archivo por fila (sin repetir) de los que quedaron en Cloudinary: base para borrarlos del disco."""
    subidos = {r.ruta_rel: r for r in registros if r.estado == SUBIDO}
    with ruta.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["nombre", "ruta_local", "public_id", "url"])
        for ruta_rel in sorted(subidos):
            r = subidos[ruta_rel]
            w.writerow([PurePosixPath(ruta_rel).name, ruta_rel, r.public_id, r.url])


def escribir_reporte(registros: list[Registro], ruta: Path, interrumpido: bool) -> str:
    """% éxito = subidos / (subidos + fallidos): sobre lo que se intentó subir. Los omitidos
    (URL externa, sin extensión) y los pendientes (corrida interrumpida o --limite) van aparte."""
    conteo: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in registros:
        conteo[r.fuente.clave][r.estado or "pendiente"] += 1

    def fila(nombre: str, c: dict[str, int]) -> str:
        total = sum(c.values())
        intentables = c[SUBIDO] + c[FALLIDO]
        pct = f"{c[SUBIDO] * 100 / intentables:6.2f}%" if intentables else "   n/a "
        return (f"{nombre:<45} {total:>8} {c[SUBIDO]:>8} {c[FALLIDO]:>8} {c[OMITIDO]:>8} "
                f"{c['pendiente']:>9} {pct:>8}")

    global_ = defaultdict(int)
    for c in conteo.values():
        for k, v in c.items():
            global_[k] += v

    lineas = [
        f"Reporte de migración a Cloudinary — {datetime.now():%Y-%m-%d %H:%M:%S}",
        "CORRIDA INTERRUMPIDA: el reporte es parcial." if interrumpido else "Corrida completa.",
        "% éxito = subidos / (subidos + fallidos). Omitidos y pendientes no cuentan.",
        "",
        f"{'tabla.columna':<45} {'total':>8} {'subidos':>8} {'fallidos':>8} {'omitidos':>8} {'pendientes':>9} {'% éxito':>8}",
        "-" * 102,
        *(fila(k, conteo[k]) for k in sorted(conteo)),
        "-" * 102,
        fila("TOTAL", global_),
    ]
    texto = "\n".join(lineas) + "\n"
    ruta.write_text(texto, encoding="utf-8")
    return texto


# ---------- Borrado del disco ----------

def borrar_subidos(raiz: Path, archivo_csv: Path, confirmar: bool, existe_en_nube, conteo: Counter) -> None:
    """Borra del disco los archivos de un subidos.csv que siguen respondiendo en Cloudinary.
    Sin `confirmar` solo simula. `conteo` es del llamador para no perderlo con un Ctrl+C."""
    with archivo_csv.open(newline="", encoding="utf-8") as f:
        filas = list(csv.DictReader(f))
    if filas and not {"ruta_local", "public_id", "url"} <= filas[0].keys():
        raise ValueError(f"{archivo_csv} no tiene las columnas de subidos.csv (ruta_local, public_id, url)")

    # a.jpg y a.png en la misma carpeta comparten public_id: Cloudinary guardó solo el último
    # y la URL del otro igual responde 200, así que no se borra ninguno de los dos.
    repetidos = {p for p, n in Counter(fila["public_id"] for fila in filas).items() if n > 1}
    raiz = raiz.resolve()
    log.info("%d archivos en %s", len(filas), archivo_csv)

    for fila in filas:
        ruta_rel = fila["ruta_local"]
        ruta = (raiz / ruta_rel).resolve()
        if not ruta.is_relative_to(raiz) or ruta == raiz:
            motivo = "la ruta queda fuera de UPLOAD_ROOT"
        elif fila["public_id"] in repetidos:
            motivo = f"public_id {fila['public_id']} repetido en el CSV, Cloudinary guardó solo uno"
        elif not ruta.is_file():
            conteo["ya no existía"] += 1
            log.info("[YA NO EXISTE] %s", ruta_rel)
            continue
        elif not existe_en_nube(fila["url"]):
            motivo = f"no responde en Cloudinary ({fila['url'] or 'sin url'})"
        else:
            motivo = ""

        if not motivo and confirmar:
            try:
                ruta.unlink()
            except OSError as e:
                motivo = f"no se pudo borrar: {e}"
        if motivo:
            conteo["no borrados"] += 1
            log.error("[NO BORRADO] %s — %s", ruta_rel, motivo)
        elif confirmar:
            conteo["borrados"] += 1
            log.info("[BORRADO] %s", ruta_rel)
        else:
            conteo["se borrarían"] += 1
            log.info("[SIMULACIÓN] se borraría %s", ruta_rel)


# ---------- Comandos ----------

def probar_conexiones(cfg: config.Config) -> bool:
    ok = True

    def check(nombre: str, funcion) -> None:
        nonlocal ok
        try:
            log.info("[OK] %s: %s", nombre, funcion())
        except Exception as e:  # cada check se reporta por separado, sin cortar los demás
            ok = False
            log.error("[ERROR] %s: %s", nombre, e)

    def bd():
        conn = conectar_bd(cfg)
        try:
            existentes = columnas_existentes(conn)
            faltan = [f.clave for f in FUENTES if (f.tabla, f.columna) not in existentes]
            aviso = f" (no existen, se saltarán: {', '.join(faltan)})" if faltan else ""
            return f"conectado a {cfg.db_database}@{cfg.db_host}{aviso}"
        finally:
            conn.close()

    def cloud():
        configurar_cloudinary(cfg)
        ping_cloudinary()
        return f"cuenta {cfg.cloudinary_cloud_name} responde"

    def disco():
        if not cfg.upload_root.is_dir():
            raise FileNotFoundError(f"{cfg.upload_root} no es un directorio")
        next(os.scandir(cfg.upload_root), None)
        return f"{cfg.upload_root} legible"

    check("Base de datos", bd)
    check("Cloudinary", cloud)
    check("UPLOAD_ROOT", disco)
    check("Endpoint", ClienteMigracion(cfg).probar)
    return ok


def migrar(cfg: config.Config, args) -> int:
    fuentes = list(FUENTES)
    if args.tablas:
        pedidas = {t.strip() for t in args.tablas.split(",") if t.strip()}
        desconocidas = pedidas - {f.tabla for f in FUENTES}
        if desconocidas:
            log.error("Tablas que no están en fuentes.py: %s", ", ".join(sorted(desconocidas)))
            return 1
        fuentes = [f for f in FUENTES if f.tabla in pedidas]

    salida = config.DIRECTORIO_SCRIPT / "salida" / f"{datetime.now():%Y%m%d_%H%M%S}"
    salida.mkdir(parents=True)
    agregar_log_archivo(salida / "migracion.log")

    registros: list[Registro] = []
    interrumpido, codigo = True, 1
    try:
        log.info("Indexando %s ...", cfg.upload_root)
        indice = indexar_disco(cfg.upload_root)
        log.info("%d nombres de archivo en disco", len(indice))

        conn = conectar_bd(cfg)
        try:
            pendientes = recolectar(conn, cfg, fuentes, indice, registros)
        finally:
            conn.close()

        subir(ClienteMigracion(cfg), cfg, pendientes, args)
        interrumpido = False
        codigo = 1 if any(r.estado == FALLIDO for r in registros) else 0
    except ErrorFatalApi as e:
        log.critical("Migración abortada: %s", e)
        codigo = 2
    except KeyboardInterrupt:
        log.warning("Interrumpido por el usuario (Ctrl+C).")
        codigo = 130
    except (pymysql.MySQLError, OSError) as e:
        log.critical("Migración abortada: %s", e)
        codigo = 2
    finally:
        escribir_csv(registros, salida / "resultados.csv")
        escribir_subidos(registros, salida / "subidos.csv")
        print("\n" + escribir_reporte(registros, salida / "reporte.txt", interrumpido))
        log.info("Salidas en %s", salida)

    return codigo


def borrar(cfg: config.Config, args) -> int:
    salida = config.DIRECTORIO_SCRIPT / "salida" / f"{datetime.now():%Y%m%d_%H%M%S}_borrado"
    salida.mkdir(parents=True)
    agregar_log_archivo(salida / "borrado.log")
    if not args.confirmar:
        log.warning("SIMULACIÓN: no se borra nada. Agrega --confirmar para borrar de verdad.")

    conteo: Counter = Counter()
    try:
        borrar_subidos(cfg.upload_root, Path(args.borrar_subidos), args.confirmar, existe_en_url, conteo)
        codigo = 1 if conteo["no borrados"] else 0
    except KeyboardInterrupt:
        log.warning("Interrumpido por el usuario (Ctrl+C).")
        codigo = 130
    except (OSError, ValueError, csv.Error, KeyError) as e:
        log.critical("No se pudo leer el CSV: %s", e)
        codigo = 2
    finally:
        log.info("Resumen: %s", ", ".join(f"{k}={v}" for k, v in sorted(conteo.items())) or "nada procesado")
        log.info("Log en %s", salida / "borrado.log")
    return codigo


# ---------- CLI ----------

def agregar_log_archivo(ruta: Path) -> None:
    manejador = logging.FileHandler(ruta, encoding="utf-8")
    manejador.setFormatter(logging.Formatter("%(asctime)s %(levelname)-8s %(message)s"))
    log.addHandler(manejador)


def entero_en_rango(minimo: int, maximo: int):
    def convertir(texto: str) -> int:
        valor = int(texto)
        if not minimo <= valor <= maximo:
            raise argparse.ArgumentTypeError(f"debe estar entre {minimo} y {maximo}")
        return valor
    return convertir


def main() -> int:
    parser = argparse.ArgumentParser(description="Migra a Cloudinary los archivos del disco de uploads referenciados en la BD.")
    parser.add_argument("--probar-conexiones", action="store_true",
                        help="Solo prueba BD, Cloudinary, UPLOAD_ROOT y el endpoint; no sube nada.")
    parser.add_argument("--tablas", help="Lista de tablas separadas por coma (por defecto, todas las de fuentes.py).")
    parser.add_argument("--lote", type=entero_en_rango(1, 20), default=5,
                        help="Archivos por petición (1-20, por defecto 5).")
    parser.add_argument("--max-mb-lote", type=entero_en_rango(1, 100), default=8,
                        help="Peso máximo por petición en MB (por defecto 8 = post_max_size por defecto de PHP).")
    parser.add_argument("--limite", type=entero_en_rango(1, 10**9),
                        help="Sube como máximo N archivos únicos (para pruebas).")
    parser.add_argument("--borrar-subidos", metavar="CSV",
                        help="Borra del disco los archivos de un subidos.csv que responden en Cloudinary. "
                             "Sin --confirmar solo simula.")
    parser.add_argument("--confirmar", action="store_true", help="Con --borrar-subidos: borra de verdad.")
    args = parser.parse_args()
    if args.confirmar and not args.borrar_subidos:
        parser.error("--confirmar solo se usa con --borrar-subidos")

    # La consola de Windows (cp1252) no imprime tildes ni flechas sin esto.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-8s %(message)s", stream=sys.stdout)

    try:
        cfg = config.cargar()
    except config.ErrorConfiguracion as e:
        log.error("%s", e)
        return 1

    if args.probar_conexiones:
        return 0 if probar_conexiones(cfg) else 1

    if args.borrar_subidos:
        return borrar(cfg, args)

    configurar_cloudinary(cfg)
    return migrar(cfg, args)


if __name__ == "__main__":
    sys.exit(main())
