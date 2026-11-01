"""Valida un tema subido y deja una copia limpia. Viene de cualquiera: nada se ejecuta ni se usa
tal cual. Solo depende de Pillow, y corre en un trabajo sin secretos.

    python tools/validar_tema.py ENTRADA CARPETA_DE_SALIDA [NICK]

Sale con 0 y escribe en la salida la carpeta del tema e info.json; si se rechaza, sale con 1 y
escribe el motivo en rechazo.txt. Las reglas son las de DowP (core/utils/theme_package.py e
theme_io.py), más estas: 20 MB como máximo y sin fuentes propias.
"""
import hashlib
import io
import json
import os
import re
import stat
import sys
import unicodedata
import zipfile
from datetime import datetime, timezone

from PIL import Image, ImageOps

MAX_BYTES = 20 * 1024 * 1024
MAX_CAPTURES = 3
MAX_CAPTURE_BYTES = 8 * 1024 * 1024
CAPTURE_SIDE = 1920
ENVELOPE_MAGIC = b"DOWPENV1"
MAX_JSON_BYTES = 1024 * 1024
MAX_ENTRIES = 6
QUALITY = 90
MAX_SIDES = {"general": 3840, "previews": 1080}

_ENTRY_RE = re.compile(r"^(manifest\.json|tema\.json|imagenes/(general|previews)\.(png|jpg|jpeg|webp))$")
_COLOR_RE = re.compile(
    r"^(#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})"
    r"|rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*(?:,\s*(?:0|1|0?\.\d+|\d{1,3})\s*)?\)"
    r"|transparent)$")
_TOKEN_RE = re.compile(r"^[a-z0-9_]{1,60}$")
_FONT_RE = re.compile(r"^[\w][\w .+&'(),-]{0,99}$")
SHAPE_LIMITS = {"radio": (0, 14), "radio_chico": (0, 8), "radio_grande": (0, 20),
                "borde": (0, 3), "tamano_fuente": (8, 22)}
FITS = ("fill", "fit", "stretch", "original", "tile")
ALIGNS = tuple(f"{v}-{h}" for v in ("top", "center", "bottom") for h in ("left", "center", "right"))


class Rechazado(Exception):
    pass


def clean_text(value, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return "".join(c for c in value if c not in "<>" and c.isprintable()).strip()[:limit]


def clean_font(value) -> str:
    return value if isinstance(value, str) and _FONT_RE.match(value) else ""


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:40] or "tema"


def is_number(key, value) -> bool:
    low, high = SHAPE_LIMITS[key]
    return not isinstance(value, bool) and isinstance(value, (int, float)) and low <= value <= high


def percent(value, default):
    return max(0, min(100, value)) if isinstance(value, int) and not isinstance(value, bool) else default


def valid_color(raw) -> bool:
    values = raw if isinstance(raw, list) else [raw]
    if isinstance(raw, list) and len(raw) != 2:
        return False
    return all(isinstance(v, str) and _COLOR_RE.match(v.strip()) for v in values)


def clean_theme(data: dict) -> dict:
    if not isinstance(data, dict):
        raise Rechazado("El archivo no contiene un tema.")
    raw_colors = data.get("colores") if isinstance(data.get("colores"), dict) else {}
    colors = {k: v for k, v in raw_colors.items() if isinstance(k, str) and _TOKEN_RE.match(k) and valid_color(v)}
    if not colors:
        raise Rechazado("El tema no tiene ningún color válido.")
    meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
    clean = {"meta": {"nombre": clean_text(meta.get("nombre"), 60), "autor": clean_text(meta.get("autor"), 60),
                      "version": clean_text(meta.get("version"), 20) or "1.0", "formato": 2},
             "colores": colors}
    shapes = data.get("formas") if isinstance(data.get("formas"), dict) else {}
    formas = {k: v for k, v in shapes.items() if k in SHAPE_LIMITS and is_number(k, v)}
    if formas:
        clean["formas"] = formas
    typography = data.get("tipografia") if isinstance(data.get("tipografia"), dict) else {}
    tipografia = {}
    size = typography.get("tamano", typography.get("tamaño"))
    if is_number("tamano_fuente", size):
        tipografia["tamano"] = size
    family = clean_font(typography.get("familia"))
    if family:
        tipografia["familia"] = family
    if tipografia:
        clean["tipografia"] = tipografia
    return clean


def clean_image(place: str, content: bytes) -> tuple:
    """Solo pasan los píxeles: se decodifica y se guarda de nuevo, siempre."""
    try:
        with Image.open(io.BytesIO(content)) as im:
            if im.format not in ("PNG", "JPEG", "WEBP"):
                raise ValueError(im.format)
            im.seek(0)
            frame = ImageOps.exif_transpose(im)
            has_alpha = frame.mode in ("RGBA", "LA", "PA") or (frame.mode == "P" and "transparency" in frame.info)
            frame = frame.convert("RGBA" if has_alpha else "RGB")
    except Exception:
        raise Rechazado("Una imagen del paquete no es válida.")
    if has_alpha and frame.getextrema()[3][0] == 255:
        frame = frame.convert("RGB")
    frame.thumbnail((MAX_SIDES[place], MAX_SIDES[place]), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    if frame.mode == "RGBA":
        frame.save(out, "WEBP", quality=QUALITY, method=4, alpha_quality=100)
        return f"{place}.webp", out.getvalue(), frame
    frame.save(out, "JPEG", quality=QUALITY, optimize=True)
    return f"{place}.jpg", out.getvalue(), frame


def clean_appearance(raw, files: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None

    def number(key, low, high):
        value = raw.get(key)
        return max(low, min(high, value)) if isinstance(value, int) and not isinstance(value, bool) else None

    clean = {"panel_opacity": number("panel_opacity", 30, 100) or 100,
             "previews_transparent": raw.get("previews_transparent") is True,
             "preview_checker": raw.get("preview_checker") is not False,
             "radius_override": number("radius_override", 0, 14),
             "font_size_override": number("font_size_override", 8, 22),
             "font_family": clean_font(raw.get("font_family")) or None,
             "places": {}}
    places = raw.get("places") if isinstance(raw.get("places"), dict) else {}
    for place in MAX_SIDES:
        settings = places.get(place)
        if not isinstance(settings, dict) or place not in files:
            continue
        name = settings.get("name")
        clean["places"][place] = {
            "file": files[place], "name": clean_text(os.path.basename(name), 200) if isinstance(name, str) else files[place],
            "opacity": percent(settings.get("opacity"), 30),
            "fit": settings.get("fit") if settings.get("fit") in FITS else "fill",
            "align": settings.get("align") if settings.get("align") in ALIGNS else "center-center",
            "blur": settings.get("blur") is True}
    return clean


def read_entry(zf: zipfile.ZipFile, info: zipfile.ZipInfo, limit: int) -> bytes:
    """Con tope real, sin creer el tamaño que declara el zip."""
    with zf.open(info) as f:
        content = f.read(limit + 1)
    if len(content) > limit:
        raise Rechazado("El paquete contiene un archivo demasiado grande.")
    return content


def read_package(path: str) -> tuple:
    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            if len(infos) > MAX_ENTRIES:
                raise Rechazado("El paquete contiene demasiados archivos.")
            names = [i.filename for i in infos]
            if len({n.casefold() for n in names}) != len(names):
                raise Rechazado("El paquete contiene archivos repetidos.")
            for info in infos:
                if info.filename.startswith("fuentes/"):
                    raise Rechazado("Por ahora no se aceptan temas con fuentes propias.")
                if not _ENTRY_RE.match(info.filename) or stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 0x1:
                    raise Rechazado("El paquete contiene un archivo no permitido.")
            if "manifest.json" not in names or "tema.json" not in names:
                raise Rechazado("El archivo no es un paquete de tema válido.")
            by_name = {i.filename: i for i in infos}
            try:
                manifest = json.loads(read_entry(zf, by_name["manifest.json"], MAX_JSON_BYTES).decode("utf-8"))
                data = json.loads(read_entry(zf, by_name["tema.json"], MAX_JSON_BYTES).decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise Rechazado("El archivo no es un paquete de tema válido.")
            if not isinstance(manifest, dict) or manifest.get("tipo") != "dowp-tema" or manifest.get("formato") != 1:
                raise Rechazado("El archivo no es un paquete de tema válido.")
            images, files, frames = {}, {}, {}
            for info in infos:
                if info.filename.startswith("imagenes/"):
                    place = os.path.splitext(info.filename.split("/", 1)[1])[0]
                    if place in files:
                        raise Rechazado("El paquete contiene archivos repetidos.")
                    name, content, frame = clean_image(place, read_entry(zf, info, MAX_BYTES))
                    images[name], files[place], frames[place] = content, name, frame
    except zipfile.BadZipFile:
        raise Rechazado("El archivo no es un paquete de tema válido.")
    theme = clean_theme(data)
    appearance = clean_appearance(data.get("apariencia"), files)
    if appearance:
        theme["apariencia"] = appearance
    return theme, images, frames


def color_at(theme: dict, key: str, fallback: str, index: int = 1) -> tuple:
    value = theme["colores"].get(key, fallback)
    value = value[index] if isinstance(value, list) else value
    match = re.fullmatch(r"#([0-9a-fA-F]{6})", value.strip())
    return tuple(int(match.group(1)[i:i + 2], 16) for i in (0, 2, 4)) if match else tuple(int(fallback[i:i + 2], 16) for i in (1, 3, 5))


def split_envelope(path: str, folder: str) -> tuple:
    """El formulario manda el tema y sus capturas juntos: DOWPENV1 + largo de la cabecera (4 bytes)
    + {"tema": n, "capturas": [n, ...]} + los archivos seguidos. Sin esa marca, es un tema suelto.
    Devuelve (ruta del tema, [bytes de cada captura])."""
    with open(path, "rb") as f:
        if f.read(len(ENVELOPE_MAGIC)) != ENVELOPE_MAGIC:
            return path, []
        size = int.from_bytes(f.read(4), "big")
        if not 0 < size <= 1024:
            raise Rechazado("El envío no es válido.")
        try:
            header = json.loads(f.read(size).decode("utf-8"))
            theme_size, captures = header["tema"], header["capturas"]
        except (UnicodeDecodeError, ValueError, KeyError, TypeError):
            raise Rechazado("El envío no es válido.")
        sizes = [theme_size] + captures if isinstance(captures, list) else []
        if not sizes or not all(isinstance(n, int) and not isinstance(n, bool) and n > 0 for n in sizes):
            raise Rechazado("El envío no es válido.")
        if theme_size > MAX_BYTES:
            raise Rechazado("El tema pasa de 20 MB.")
        if len(captures) > MAX_CAPTURES:
            raise Rechazado("Solo se aceptan hasta 3 capturas.")
        if any(n > MAX_CAPTURE_BYTES for n in captures):
            raise Rechazado("Cada captura puede pesar hasta 8 MB.")
        theme_path = os.path.join(folder, "tema_recibido.bin")
        content = f.read(theme_size)
        if len(content) != theme_size:
            raise Rechazado("El envío no es válido.")
        with open(theme_path, "wb") as out:
            out.write(content)
        pictures = []
        for n in captures:
            picture = f.read(n)
            if len(picture) != n:
                raise Rechazado("El envío no es válido.")
            pictures.append(picture)
        if f.read(1):
            raise Rechazado("El envío no es válido.")
    return theme_path, pictures


def clean_capture(content: bytes):
    """Una captura: solo sus píxeles, sin transparencia ni metadatos."""
    try:
        with Image.open(io.BytesIO(content)) as im:
            if im.format not in ("PNG", "JPEG", "WEBP"):
                raise ValueError(im.format)
            im.seek(0)
            frame = ImageOps.exif_transpose(im).convert("RGBA")
    except Exception:
        raise Rechazado("Una de las capturas no es una imagen válida.")
    frame.thumbnail((CAPTURE_SIDE, CAPTURE_SIDE), Image.Resampling.LANCZOS)
    flat = Image.new("RGB", frame.size, (16, 16, 16))
    flat.paste(frame, (0, 0), frame)
    return flat


def jpeg_bytes(image, quality: int = 85) -> bytes:
    out = io.BytesIO()
    image.save(out, "JPEG", quality=quality, optimize=True)
    return out.getvalue()


def sample_colors(theme: dict) -> list:
    """Los colores principales, para mostrarlos en la ficha del tema."""
    keys = ("fondo_principal", "fondo_secundario", "acento_primario", "boton_fondo_inicio", "texto_principal", "estado_exito")
    colors = []
    for key in keys:
        red, green, blue = color_at(theme, key, "#808080")
        colors.append(f"#{red:02x}{green:02x}{blue:02x}")
    return colors


def make_preview(theme: dict, frames: dict) -> bytes:
    """La vista por defecto si el autor no sube capturas."""
    canvas = Image.new("RGB", (640, 360), color_at(theme, "fondo_principal", "#101010"))
    general = frames.get("general")
    if general is not None:
        picture = ImageOps.fit(general.convert("RGBA"), (640, 360), Image.Resampling.LANCZOS)
        canvas.paste(picture, (0, 0), picture)
    keys = ("fondo_secundario", "acento_primario", "boton_fondo_inicio", "texto_principal", "estado_exito", "estado_error")
    for i, key in enumerate(keys):
        swatch = Image.new("RGB", (64, 40), color_at(theme, key, "#808080"))
        canvas.paste(swatch, (16 + i * 72, 304))
    previews = frames.get("previews")
    if previews is not None:
        small = previews.convert("RGBA")
        small.thumbnail((200, 200), Image.Resampling.LANCZOS)
        canvas.paste(small, (424, 16), small)
    out = io.BytesIO()
    canvas.save(out, "JPEG", quality=85, optimize=True)
    return out.getvalue()


def build_package(theme: dict, images: dict) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({"tipo": "dowp-tema", "formato": 1}))
        for name, content in sorted(images.items()):
            zf.writestr(f"imagenes/{name}", content)
        zf.writestr("tema.json", json.dumps(theme, ensure_ascii=False, indent=2))
    return out.getvalue()


def validate(path: str, output: str, nick: str = "") -> dict:
    os.makedirs(output, exist_ok=True)
    source = path
    path, pictures = split_envelope(path, output)
    captures = [clean_capture(picture) for picture in pictures]
    size = os.path.getsize(path)
    if size > MAX_BYTES:
        raise Rechazado("El tema pasa de 20 MB.")
    if zipfile.is_zipfile(path):
        theme, images, frames = read_package(path)
        content, extension = None, ".dthmp"
    else:
        if size > MAX_JSON_BYTES:
            raise Rechazado("El archivo no es un tema válido.")
        try:
            with open(path, encoding="utf-8-sig") as f:
                raw = json.load(f)
        except (UnicodeDecodeError, ValueError):
            raise Rechazado("El archivo no es un tema válido.")
        theme, images, frames = clean_theme(raw), {}, {}
        content, extension = None, ".json"
    name = theme["meta"]["nombre"] or "Tema sin nombre"
    theme["meta"]["nombre"] = name
    theme["meta"]["autor"] = clean_text(nick, 30)  # quien lo sube; lo del archivo no se puede comprobar
    # Con el nombre del tema, que es como se descarga. "info" chocaría con info.json.
    stem = slugify(name)
    file_name = f"{'tema-info' if stem == 'info' else stem}{extension}"
    if extension == ".dthmp":
        content = build_package(theme, images)
    else:
        content = json.dumps(theme, ensure_ascii=False, indent=2).encode("utf-8")
    if len(content) > MAX_BYTES:
        raise Rechazado("El tema pasa de 20 MB.")
    with open(source, "rb") as f:  # del original: el mismo envío da siempre el mismo identificador
        digest = hashlib.sha256(f.read()).hexdigest()[:8]
    if path != source:
        os.remove(path)
    theme_id = f"{slugify(name)}-{digest}"
    folder = os.path.join(output, theme_id)
    os.makedirs(folder)
    with open(os.path.join(folder, file_name), "wb") as f:
        f.write(content)
    if captures:
        cover = ImageOps.fit(captures[0], (640, 360), Image.Resampling.LANCZOS)
        preview = jpeg_bytes(cover)
    else:
        preview = make_preview(theme, frames)
    with open(os.path.join(folder, "vista.jpg"), "wb") as f:
        f.write(preview)
    for number, capture in enumerate(captures, 1):
        with open(os.path.join(folder, f"captura{number}.jpg"), "wb") as f:
            f.write(jpeg_bytes(capture))
    info = {"id": theme_id, "nombre": name, "autor": theme["meta"]["autor"], "archivo": file_name,
            "bytes": len(content), "colores": len(theme["colores"]), "imagenes": sorted(images),
            "capturas": len(captures), "muestra": sample_colors(theme),
            "fecha": datetime.now(timezone.utc).strftime("%Y-%m-%d")}
    with open(os.path.join(folder, "info.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
    with open(os.path.join(output, "info.json"), "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
    return info


def main() -> int:
    source, output = sys.argv[1], sys.argv[2]
    os.makedirs(output, exist_ok=True)
    try:
        info = validate(source, output, sys.argv[3] if len(sys.argv) > 3 else "")
    except Rechazado as e:
        with open(os.path.join(output, "rechazo.txt"), "w", encoding="utf-8") as f:
            f.write(str(e))
        print(f"RECHAZADO: {e}")
        return 1
    print(f"ACEPTADO: {info['id']} ({info['bytes']} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
