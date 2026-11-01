"""Escribe el texto del pull request con lo que dejó el validador. El nombre y el autor los eligió
quien subió el tema: se dejan sin marcado, para que no puedan incluir enlaces ni imágenes."""
import json
import re
import sys


def plain(text: str) -> str:
    return re.sub(r"[\\`*_{}\[\]()#+!|~@:/]", " ", text).strip() or "(sin indicar)"


info = json.load(open(sys.argv[1], encoding="utf-8"))
repo, branch = sys.argv[2], sys.argv[3]
print(f"""![Vista previa](https://raw.githubusercontent.com/{repo}/{branch}/temas/{info['id']}/vista.jpg)

| | |
|---|---|
| **Nombre** | {plain(info['nombre'])} |
| **Autor** | {plain(info['autor'])} |
| **Archivo** | {info['archivo']} |
| **Peso** | {info['bytes'] / 1048576:.2f} MB |
| **Colores** | {info['colores']} |
| **Imágenes** | {len(info['imagenes'])} |

Pasó el validador automático. Revisa la vista previa, y fusiona para publicarlo o cierra para rechazarlo.""")
