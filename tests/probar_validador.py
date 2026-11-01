"""Prueba el validador con paquetes hostiles y un tema real. Uso:
    python tests/probar_validador.py [ruta a un .dthmp real]"""
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile

from PIL import Image

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(HERE, "tools"))
import validar_tema as v

TMP = tempfile.mkdtemp()
MAN = json.dumps({"tipo": "dowp-tema", "formato": 1})
GOOD = {"meta": {"nombre": "Prueba"}, "colores": {"acento_primario": ["#112233", "#445566"]}}
BASE = [("manifest.json", MAN), ("tema.json", json.dumps(GOOD))]
failures = 0


def image(fmt="JPEG", size=(64, 64), mode="RGB"):
    out = io.BytesIO()
    Image.new(mode, size, (9, 9, 9) if mode == "RGB" else (9, 9, 9, 0)).save(out, fmt)
    return out.getvalue()


def package(entries, link=False):
    path = os.path.join(TMP, f"p{len(os.listdir(TMP))}.dthmp")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in entries:
            z.writestr(name, content)
        if link:
            info = zipfile.ZipInfo("imagenes/general.jpg")
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            z.writestr(info, "C:/Windows/System32/config/SAM")
    return path


def rejected(tag, path):
    global failures
    out = tempfile.mkdtemp(dir=TMP)
    try:
        v.validate(path, out)
        print("MAL  aceptó:", tag); failures += 1
    except v.Rechazado as e:
        print(f"OK   {tag}: {e}")
    except Exception as e:
        print(f"MAL  {tag}: error sin controlar {type(e).__name__}: {e}"); failures += 1


def check(tag, ok):
    global failures
    print(("OK  " if ok else "MAL ") + " " + tag)
    failures += 0 if ok else 1


rejected("un .exe", package(BASE + [("virus.exe", b"MZ")]))
rejected("un .py en imagenes", package(BASE + [("imagenes/x.py", "import os")]))
rejected("sin manifiesto", package([("foto.jpg", image())]))
rejected("salir de la carpeta", package(BASE + [("imagenes/../../evil.jpg", image())]))
rejected("ruta absoluta", package(BASE + [("C:/Windows/evil.jpg", image())]))
rejected("enlace simbólico", package(BASE, link=True))
rejected("exe como .jpg", package(BASE + [("imagenes/general.jpg", b"MZ\x90\x00" + os.urandom(2048))]))
rejected("SVG como .png", package(BASE + [("imagenes/general.png", b"<svg onload=alert(1)>")]))
rejected("una fuente propia", package(BASE + [("fuentes/f.ttf", b"\x00\x01\x00\x00" + os.urandom(2048))]))
rejected("bomba en tema.json", package([("manifest.json", MAN), ("tema.json", b" " * (200 * 1024 * 1024))]))
rejected("demasiados archivos", package(BASE + [(f"imagenes/general{i}.jpg", image()) for i in range(9)]))
rejected("otro lugar de imagen", package(BASE + [("imagenes/secreto.jpg", image())]))
rejected("dos imágenes del mismo lugar", package(BASE + [("imagenes/general.jpg", image()), ("imagenes/general.png", image("PNG"))]))
rejected("sin colores", package([("manifest.json", MAN), ("tema.json", json.dumps({"meta": {}}))]))
rejected("zip dañado", (lambda p: (open(p, "wb").write(b"PK\x03\x04 roto"), p)[1])(os.path.join(TMP, "roto.dthmp")))
rejected("JSON inválido", (lambda p: (open(p, "w").write("{ no"), p)[1])(os.path.join(TMP, "x.json")))
big = os.path.join(TMP, "grande.dthmp")
with open(big, "wb") as f:
    f.truncate(v.MAX_BYTES + 1)
rejected("más de 20 MB", big)

evil = {"meta": {"nombre": "<b>../../Windows/evil</b>", "autor": "<script>alert(1)</script>"},
        "colores": {"acento_primario": "#112233", "texto_clave": "red; } * {}", "../x": "#000000"},
        "formas": {"radio": "0; } QWidget {}", "borde": 99999, "radio_chico": 3, "inventada": 1},
        "tipografia": {"familia": 'Inter"; } * { image: url(x); } /*', "tamano": "10; }"},
        "basado_en": "../../otra", "script": "print(1)",
        "apariencia": {"font_family": 'x"; }', "panel_opacity": 99999, "places": {"general": {"file": "../../x.jpg"}}, "extra": 1}}
out = tempfile.mkdtemp(dir=TMP)
info = v.validate(package([("manifest.json", MAN), ("tema.json", json.dumps(evil))]), out)
data = json.loads(zipfile.ZipFile(os.path.join(out, info["id"], info["archivo"])).read("tema.json"))
check("JSON hostil: solo quedan claves conocidas", set(data) == {"meta", "colores", "formas", "apariencia"})
check("JSON hostil: colores limpios", list(data["colores"]) == ["acento_primario"])
check("JSON hostil: formas limpias", data["formas"] == {"radio_chico": 3})
check("JSON hostil: apariencia limpia", data["apariencia"]["font_family"] is None and data["apariencia"]["places"] == {} and data["apariencia"]["panel_opacity"] == 100 and "extra" not in data["apariencia"])
check("JSON hostil: sin marcado ni rutas en el id", "<" not in data["meta"]["nombre"] and "/" not in info["id"] and "." not in info["id"])

out = tempfile.mkdtemp(dir=TMP)
info = v.validate(package(BASE + [("imagenes/general.jpg", image() + b"PK\x03\x04 script"), ("imagenes/previews.png", image("PNG", (2000, 2000), "RGBA"))]), out)
z = zipfile.ZipFile(os.path.join(out, info["id"], info["archivo"]))
check("lo pegado tras la imagen desaparece", b"script" not in z.read("imagenes/general.jpg"))
check("la vista previa pasa a WebP de 1080 px", Image.open(io.BytesIO(z.read("imagenes/previews.webp"))).size == (1080, 1080))
check("genera vista.jpg", Image.open(os.path.join(out, info["id"], "vista.jpg")).size == (640, 360))

out = tempfile.mkdtemp(dir=TMP)
path = os.path.join(TMP, "t.json")
with open(path, "w", encoding="utf-8") as f:
    json.dump(GOOD, f)
info = v.validate(path, out)
check("acepta un .json, con el nombre del tema", info["archivo"] == "prueba.json")

body = subprocess.run([sys.executable, os.path.join(HERE, "tools", "cuerpo_pr.py"), os.path.join(out, "info.json"), "a/b", "tema/x"], capture_output=True, text=True, encoding="utf-8")
check("el texto del pull request se genera", body.returncode == 0 and "vista.jpg" in body.stdout)
evil_info = os.path.join(TMP, "i.json")
with open(evil_info, "w", encoding="utf-8") as f:
    json.dump(dict(info, nombre="[clic](http://evil) ![x](http://evil/y)", autor="@alguien"), f)
body = subprocess.run([sys.executable, os.path.join(HERE, "tools", "cuerpo_pr.py"), evil_info, "a/b", "tema/x"], capture_output=True, text=True, encoding="utf-8")
check("el texto no permite enlaces, imágenes ni menciones", "evil)" not in body.stdout and "](" not in body.stdout.split("|", 1)[1] and "@" not in body.stdout)

out = tempfile.mkdtemp(dir=TMP)
info = v.validate(path, out, "  <b>Nick</b> " + "x" * 50)
check("el nick se limpia y se recorta", info["autor"] == "bNick/b " + "x" * 22)
out = tempfile.mkdtemp(dir=TMP)
check("sin nick no queda el autor del archivo", v.validate(package(BASE + [("imagenes/general.jpg", image())]), out)["autor"] == "")

out = tempfile.mkdtemp(dir=TMP)
named = dict(GOOD, meta={"nombre": "Info"})
info = v.validate(package([("manifest.json", MAN), ("tema.json", json.dumps(named))]), out)
check("un tema llamado Info no pisa info.json", info["archivo"] == "tema-info.dthmp" and os.path.isfile(os.path.join(out, info["id"], "info.json")))
out = tempfile.mkdtemp(dir=TMP)
named = dict(GOOD, meta={"nombre": "../../Ñandú <b>Rojo</b>"})
info = v.validate(package([("manifest.json", MAN), ("tema.json", json.dumps(named))]), out)
check("el nombre del archivo no permite rutas", "/" not in info["archivo"] and ".." not in info["archivo"].replace(".dthmp", ""))


def envelope(theme, pictures, header=None, extra=b""):
    head = json.dumps(header or {"tema": len(theme), "capturas": [len(c) for c in pictures]}).encode()
    path = os.path.join(TMP, f"env{len(os.listdir(TMP))}.bin")
    with open(path, "wb") as f:
        f.write(v.ENVELOPE_MAGIC + len(head).to_bytes(4, "big") + head + theme + b"".join(pictures) + extra)
    return path


theme_bytes = json.dumps(GOOD).encode()
out = tempfile.mkdtemp(dir=TMP)
info = v.validate(envelope(theme_bytes, [image("PNG", (2600, 1400)), image("JPEG", (800, 450))]), out, "Marck")
folder = os.path.join(out, info["id"])
check("capturas: guarda las dos y una carátula", info["capturas"] == 2 and sorted(f for f in os.listdir(folder) if f.endswith(".jpg")) == ["captura1.jpg", "captura2.jpg", "vista.jpg"])
check("capturas: reduce a 1920 px", max(Image.open(os.path.join(folder, "captura1.jpg")).size) == 1920)
check("capturas: la carátula sale de la primera", Image.open(os.path.join(folder, "vista.jpg")).size == (640, 360))
check("muestra de colores", len(info["muestra"]) == 6 and all(c.startswith("#") for c in info["muestra"]))
check("no deja archivos temporales", not os.path.exists(os.path.join(out, "tema_recibido.bin")))
hidden = image() + b"PK\x03\x04 script"
out = tempfile.mkdtemp(dir=TMP)
info = v.validate(envelope(theme_bytes, [hidden]), out)
check("capturas: lo pegado desaparece", b"script" not in open(os.path.join(out, info["id"], "captura1.jpg"), "rb").read())
rejected("captura que no es imagen", envelope(theme_bytes, [b"<svg onload=alert(1)>"]))
rejected("exe como captura", envelope(theme_bytes, [b"MZ\x90\x00" + os.urandom(2048)]))
rejected("más de 3 capturas", envelope(theme_bytes, [image()] * 4))
rejected("captura de más de 8 MB", envelope(theme_bytes, [b"x"], {"tema": len(theme_bytes), "capturas": [v.MAX_CAPTURE_BYTES + 1]}))
rejected("cabecera que miente", envelope(theme_bytes, [image()], {"tema": len(theme_bytes), "capturas": [999999]}))
rejected("datos de más al final", envelope(theme_bytes, [image()], extra=b"secreto"))
rejected("cabecera inválida", envelope(theme_bytes, [], {"tema": -5, "capturas": []}))
rejected("cabecera con tipos raros", envelope(theme_bytes, [], {"tema": True, "capturas": "x"}))

if len(sys.argv) > 1:
    out = tempfile.mkdtemp(dir=TMP)
    info = v.validate(sys.argv[1], out)
    print("TEMA REAL:", json.dumps(info, ensure_ascii=False))
    print("carpeta de salida:", os.path.join(out, info["id"]))
    shutil.copy(os.path.join(out, info["id"], "vista.jpg"), os.path.join(HERE, "tests", "vista_ejemplo.jpg"))

print("\nFALLOS:", failures)
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if failures else 0)
