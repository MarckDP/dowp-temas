"""Escribe el texto del pull request con lo que dejó el validador. El nombre y el autor los eligió
quien subió el tema: se dejan sin marcado, para que no puedan incluir enlaces ni imágenes."""
import json
import re
import sys


def plain(text: str) -> str:
    return re.sub(r"[\\`*_{}\[\]()#+!|~@:/]", " ", text).strip() or "(sin indicar)"


info = json.load(open(sys.argv[1], encoding="utf-8"))
repo, branch = sys.argv[2], sys.argv[3]
base = f"https://raw.githubusercontent.com/{repo}/{branch}/temas/{info['id']}"
pictures = [f"![Captura {n}]({base}/captura{n}.jpg)" for n in range(1, info.get("capturas", 0) + 1)]
print("\n\n".join(pictures) or f"![Vista previa]({base}/vista.jpg)")
print(f"""

| | |
|---|---|
| **Nombre** | {plain(info['nombre'])} |
| **Autor** | {plain(info['autor'])} |
| **Archivo** | {info['archivo']} |
| **Peso** | {info['bytes'] / 1048576:.2f} MB |
| **Colores** | {info['colores']} |
| **Imágenes** | {len(info['imagenes'])} |
| **Capturas** | {info.get('capturas', 0)} |

Pasó el validador automático. Revisa la vista previa, y fusiona para publicarlo o cierra para rechazarlo.""")
