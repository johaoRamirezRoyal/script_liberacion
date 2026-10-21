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


nohup python migrar.py > corrida.out 2>&1 & tail -f corrida.out 
```
Para revisarla después:
```bash
ps aux | grep migrar.py          # ¿sigue corriendo?
tail -f corrida.out              # volver a ver el avance
kill <PID>                       # detenerla; el reporte parcial queda escrito igual
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
| `subidos.csv` | Un archivo por fila, sin repetir, de los que quedaron en Cloudinary: nombre, ruta_local (relativa a `UPLOAD_ROOT`), public_id, url |
| `reporte.txt` | Por tabla.columna: total, subidos, fallidos, omitidos, pendientes y % de éxito |

% éxito = subidos / (subidos + fallidos). Omitidos = ya era una URL o el valor no tiene
extensión. Pendientes = no se alcanzaron a procesar (Ctrl+C, error fatal o `--limite`).

Código de salida: 0 sin fallidos, 1 con fallidos o configuración incompleta, 2 si se
abortó (token rechazado, endpoint apagado, error de BD o disco), 130 con Ctrl+C.

## Borrar del servidor los archivos ya migrados

Usa el `subidos.csv` de una corrida. **Por defecto solo simula**; borra únicamente con `--confirmar`.

```bash
python migrar.py --borrar-subidos salida/<fecha>/subidos.csv              # simulación: qué se borraría
python migrar.py --borrar-subidos salida/<fecha>/subidos.csv --confirmar  # borra de verdad
```

Antes de borrar cada archivo verifica que:
- la ruta quede dentro de `UPLOAD_ROOT` (no acepta `..` ni rutas absolutas);
- su `url` responda 200 en Cloudinary (HEAD a la URL pública, no gasta cuota del Admin API);
- su `public_id` no se repita en el CSV (p. ej. `a.jpg` y `a.png` en la misma carpeta:
  Cloudinary guardó solo uno, así que no se borra ninguno).

Si algo falla, el archivo **no** se borra y queda como `[NO BORRADO]` con el motivo.
Salida en `salida/<fecha>_borrado/borrado.log`: una línea por archivo (`[BORRADO]`,
`[SIMULACIÓN]`, `[NO BORRADO]`, `[YA NO EXISTE]`) y un resumen al final. Repetirlo es seguro:
lo ya borrado sale como `[YA NO EXISTE]`.

Recomendado: hacer un backup de `UPLOAD_ROOT` antes del primer `--confirmar`
(`tar czf uploads_backup.tgz -C /home/royal/sami_royal/public upload`).
Código de salida: 0 sin `[NO BORRADO]`, 1 con alguno, 2 si el CSV no se pudo leer, 130 con Ctrl+C.

## Al terminar

Borrar `MIGRACION_API_TOKEN` del `.env` del backend y correr `php artisan config:cache`:
el endpoint vuelve a responder 404.
