"""Crea la carpeta que se publica en GitHub Pages: los temas aprobados y temas.json con su lista."""
import json
import os
import shutil
import sys

output = sys.argv[1]
os.makedirs(output, exist_ok=True)
open(os.path.join(output, ".nojekyll"), "w").close()
themes = []
if os.path.isdir("temas"):
    shutil.copytree("temas", os.path.join(output, "temas"))
    for name in os.listdir("temas"):
        path = os.path.join("temas", name, "info.json")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as f:
                themes.append(json.load(f))
themes.sort(key=lambda t: (t.get("fecha", ""), t.get("id", "")), reverse=True)
with open(os.path.join(output, "temas.json"), "w", encoding="utf-8") as f:
    json.dump(themes, f, ensure_ascii=False, indent=2)
print(f"{len(themes)} temas")
