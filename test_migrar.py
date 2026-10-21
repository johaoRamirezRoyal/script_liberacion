"""Prueba de la lógica sin BD ni red: python test_migrar.py"""

import tempfile
from collections import Counter
from pathlib import Path

from fuentes import Fuente
from migrar import FALLIDO, OMITIDO, SUBIDO, Registro, armar_lotes, borrar_subidos, escribir_reporte, escribir_subidos, indexar_disco, resolver_ruta


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

        regs += [Registro(f, 5, "b.pdf", ruta_rel="tramites/documentos/b.pdf", estado=SUBIDO),
                 Registro(f, 6, "b.pdf", ruta_rel="tramites/documentos/b.pdf", estado=SUBIDO)]
        escribir_subidos(regs, raiz / "s.csv")
        filas = (raiz / "s.csv").read_text(encoding="utf-8").splitlines()
        assert len(filas) == 3 and filas[2].startswith("b.pdf,tramites/documentos/b.pdf")  # sin repetir

        # Borrado: c.png no responde en la nube, a.jpg y a.png chocan por public_id, ../x sale de la raíz.
        (raiz / "a.png").write_bytes(b"x")
        (raiz / "b.csv").write_text(
            "nombre,ruta_local,public_id,url\n"
            "b.pdf,tramites/documentos/b.pdf,royal/tramites/documentos/b,u-b\n"
            "c.png,c.png,royal/c,u-c\n"
            "a.jpg,a.jpg,royal/a,u-a\n"
            "a.png,a.png,royal/a,u-a2\n"
            "x,../x,royal/x,u-x\n"
            "z.jpg,z.jpg,royal/z,u-z\n", encoding="utf-8")
        en_nube = lambda url: url != "u-c"
        conteo = Counter()
        borrar_subidos(raiz, raiz / "b.csv", False, en_nube, conteo)
        assert conteo == Counter({"se borrarían": 1, "no borrados": 4, "ya no existía": 1}), conteo
        assert (raiz / "tramites/documentos/b.pdf").exists()  # simulación: no toca nada
        conteo = Counter()
        borrar_subidos(raiz, raiz / "b.csv", True, en_nube, conteo)
        assert conteo["borrados"] == 1 and not (raiz / "tramites/documentos/b.pdf").exists()
        assert all((raiz / n).exists() for n in ("a.jpg", "a.png", "c.png"))
    print("OK")


if __name__ == "__main__":
    test()
