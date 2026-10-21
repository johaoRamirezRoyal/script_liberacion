# script_liberacion — migración de archivos a Cloudinary

Recorre las tablas de `fuentes.py`, busca cada archivo en el disco de uploads y lo sube a
Cloudinary a través del endpoint `POST /api/migracion/cloudinary` del backend.
Carpeta en Cloudinary: `royal/<subcarpeta original>/<nombre>` (los de la raíz quedan en `royal/`).
La BD **no** se modifica.

## Instalación (en el VPS)

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # completar
```

Backend: agregar `MIGRACION_API_TOKEN=<token largo>` a su `.env` y correr
`php artisan config:cache`. Usar el mismo token en el `.env` de este script.

## Uso

```bash
python migrar.py --probar-conexiones              # BD, Cloudinary, UPLOAD_ROOT y endpoint
python migrar.py --tablas firmas --limite 20      # prueba chica
python migrar.py                                  # todo
python migrar.py --lote 5 --max-mb-lote 8         # tamaño de cada petición
python test_migrar.py                             # prueba de la lógica (sin BD ni red)
```

- `--lote` (1-20, por defecto 5): archivos por petición. Bajarlo si PHP corta por
  `max_execution_time`.
- `--max-mb-lote` (por defecto 8): peso por petición; no puede superar `post_max_size` de PHP.
- Repetir la corrida es seguro: el `public_id` es el nombre del archivo y se sobrescribe.

## Salidas (`salida/<fecha>/`)

| Archivo | Contenido |
|---|---|
| `migracion.log` | Una línea por archivo: `[OK]` con la URL, `[ERROR]` con el motivo, `[OMITIDO]` |
| `resultados.csv` | tabla, columna, id, valor original, ruta local, estado, public_id, url, mensaje |
| `reporte.txt` | Por tabla.columna: total, subidos, fallidos, omitidos, pendientes y % de éxito |

% éxito = subidos / (subidos + fallidos). Omitidos = ya era una URL o el valor no tiene
extensión. Pendientes = no se alcanzaron a procesar (Ctrl+C, error fatal o `--limite`).

Código de salida: 0 sin fallidos, 1 con fallidos o configuración incompleta, 2 si se
abortó (token rechazado, endpoint apagado, error de BD o disco), 130 con Ctrl+C.

## Al terminar

Borrar `MIGRACION_API_TOKEN` del `.env` del backend y correr `php artisan config:cache`:
el endpoint vuelve a responder 404.
