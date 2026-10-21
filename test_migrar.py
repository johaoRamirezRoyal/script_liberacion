"""Prueba de la lógica sin BD ni red: python test_migrar.py"""

import tempfile
from pathlib import Path

from fuentes import Fuente
from migrar import FALLIDO, OMITIDO, SUBIDO, Registro, armar_lotes, escribir_reporte, indexar_disco, resolver_ruta


def test():
    with tempfile.TemporaryDirectory() as tmp:
        raiz = Path(tmp)
        (raiz / "tramites/documentos").mkdir(parents=True)
        for ruta in ("a.jpg", "tramites/documentos/b.pdf", "tramites/documentos/a.jpg", "c.png"):
            (raiz / ruta).write_bytes(b"x" * 100)

        indice = indexar_disco(raiz)
        assert resolver_ruta("b.pdf", raiz, indice) == ("tramites/documentos/b.pdf", "")
        assert resolver_ruta("tramites/documentos/b.pdf", raiz, indice)[0] == "tramites/documentos/b.pdf"
        ruta, aviso = resolver_ruta("a.jpg", raiz, indice)
        assert ruta == "a.jpg" and "2 carpetas" in aviso  # duplicado: gana la raíz
        assert resolver_ruta("no.jpg", raiz, indice) == (None, "")
        assert resolver_ruta("../etc/passwd", raiz, indice)[0] is None

        lotes = list(armar_lotes(["a.jpg", "c.png", "tramites/documentos/b.pdf"], raiz, max_archivos=1, max_bytes=10**6))
        assert lotes == [("", ["a.jpg"]), ("", ["c.png"]), ("tramites/documentos", ["tramites/documentos/b.pdf"])]
        assert len(list(armar_lotes(["a.jpg", "c.png"], raiz, max_archivos=20, max_bytes=150))) == 2  # corte por peso

        f = Fuente("t", "c")
        regs = [Registro(f, 1, "a", estado=SUBIDO), Registro(f, 2, "b", estado=FALLIDO),
                Registro(f, 3, "c", estado=OMITIDO), Registro(f, 4, "d")]
        texto = escribir_reporte(regs, raiz / "r.txt", interrumpido=True)
        assert " 50.00%" in texto and "PARCIAL" in texto.upper()  # 1 subido / (1 subido + 1 fallido)
    print("OK")


if __name__ == "__main__":
    test()
