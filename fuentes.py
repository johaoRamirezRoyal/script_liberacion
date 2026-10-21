"""Columnas de la BD que guardan archivos del disco de uploads (nombre o ruta relativa).

No están las que ya apuntan a Cloudinary (admisiones_documentos, cartas_recomendacion,
llegadas_tardes, noticias_revistas). Una tabla o columna que no exista en la BD se salta
con un aviso.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Fuente:
    tabla: str
    columna: str
    pk: str = "id"

    @property
    def clave(self) -> str:
        return f"{self.tabla}.{self.columna}"


FUENTES = (
    # Tablas antiguas sin modelo en Laravel
    Fuente("evidencias", "nombre"),
    Fuente("cotizacion", "cotizacion"),
    Fuente("cotizacion", "factura"),
    Fuente("cotizacion", "orden_compra"),
    Fuente("recaudo", "documento"),
    Fuente("programacion_apro", "archivo"),
    Fuente("renovacion_evidencia", "nombre"),
    Fuente("img_super_empresa", "nombre"),
    Fuente("estudiante_documentos", "documento_estudiante"),
    Fuente("estudiante_documentos", "certificado"),
    Fuente("estudiante_documentos", "documento_acudiente"),
    Fuente("programacion_herramientas", "evidencia"),
    Fuente("actividad_mensajer", "evidencia"),
    Fuente("datos_egresados", "foto"),
    # Tablas con modelo
    Fuente("documentos", "nombre"),
    Fuente("documentos_varios", "nombre_doc"),
    Fuente("tramite_documentos", "archivo"),
    Fuente("renovacion_documentos", "evidencia"),
    Fuente("proveedor_documento", "nombre"),
    Fuente("solicitudes", "cotizacion_doc"),
    Fuente("solicitud_verificacion", "factura_doc"),
    Fuente("certificado_formacion", "nombre_archivo"),
    Fuente("experiencia_laboral", "certificado_trabajo"),
    Fuente("produccion_intelectual", "evidencia_pdf"),
    Fuente("permiso", "evidencia_permiso"),
    Fuente("reportes", "evidencia"),
    Fuente("info_adicional_usuarios", "cedula_doc"),
    Fuente("foto_perfil", "nombre_foto"),
    Fuente("usuarios", "foto_carnet", pk="id_user"),
    Fuente("usuarios", "firma_digital", pk="id_user"),
    Fuente("firmas", "nombre"),
    Fuente("biblioteca_libros", "foto"),
    Fuente("capacitacion_curso", "imagen"),
    Fuente("asistencia_mensaje", "imagen"),
    Fuente("asistencia_mensaje_general", "imagen"),
    Fuente("cumpleanos_plantillas", "imagen"),
    Fuente("banner_informativo", "imagen"),
    Fuente("hce_observaciones_firmas", "firma_padre"),
    Fuente("hce_observaciones_firmas", "firma_madre"),
)
