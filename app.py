from flask import Flask, request, send_file, jsonify
from PIL import Image
import io
import requests

app = Flask(__name__)

def trim_white(img: Image.Image, tol: int = 18) -> Image.Image:
    """
    Recorta márgenes blancos de una imagen.
    tol: tolerancia (0 = recorta solo blanco puro; 10-25 suele andar bien)
    """
    # Trabajamos en RGBA para poder medir bien
    im = img.convert("RGBA")
    px = im.load()
    w, h = im.size

    def is_white(r, g, b, a):
        # Si es transparente, lo consideramos "fondo" (recortable)
        if a == 0:
            return True
        return (r >= 255 - tol) and (g >= 255 - tol) and (b >= 255 - tol)

    # Buscamos bounding box de "no-blanco"
    left = w
    right = -1
    top = h
    bottom = -1

    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if not is_white(r, g, b, a):
                if x < left: left = x
                if x > right: right = x
                if y < top: top = y
                if y > bottom: bottom = y

    # Si está todo blanco (raro), devolvemos original
    if right == -1:
        return img.convert("RGBA")

    # Expandimos 1px para no cortar sombra “agresivo”
    pad = 1
    left = max(0, left - pad)
    top = max(0, top - pad)
    right = min(w - 1, right + pad)
    bottom = min(h - 1, bottom + pad)

    return im.crop((left, top, right + 1, bottom + 1))


def download_image(url: str, timeout: int = 30) -> Image.Image:
    r = requests.get(url, timeout=timeout)
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content))


@app.route("/", methods=["GET"])
def health():
    return "OK", 200


@app.route("/render", methods=["POST"])
def render():
    data = request.get_json(force=True, silent=True) or {}

    urls = data.get("urls", [])
    if not isinstance(urls, list) or len(urls) == 0:
        return jsonify({"error": "Falta 'urls' (array) en el body"}), 400

    # Parámetros opcionales
    bg = data.get("bg", "white")          # "white" o "#ffffff"
    gap = int(data.get("gap", 0))         # separación entre módulos (0 = pegados)
    tol = int(data.get("tol", 18))        # tolerancia recorte blanco (12-25 recomendado)
    target_h = data.get("height", None)   # si querés forzar altura

    # 1) Descargar y recortar blancos
    modules = []
    for u in urls:
        print("Bajando:", u, flush=True)
        im = download_image(u)
        im = trim_white(im, tol=tol)
        modules.append(im)

    # 2) Normalizar alturas (opcional)
    if target_h is not None:
        target_h = int(target_h)
    else:
        # Usamos la altura máxima ya recortada
        target_h = max(m.size[1] for m in modules)

    resized = []
    for m in modules:
        w, h = m.size
        if h == target_h:
            resized.append(m)
        else:
            scale = target_h / float(h)
            new_w = max(1, int(w * scale))
            resized.append(m.resize((new_w, target_h), Image.LANCZOS))

    # 3) Crear canvas final (fondo blanco) y pegar alineando abajo
    total_w = sum(m.size[0] for m in resized) + gap * (len(resized) - 1)
    total_h = target_h

    # Fondo blanco (RGB)
    canvas = Image.new("RGB", (total_w, total_h), color=bg)

    x = 0
    for m in resized:
        mw, mh = m.size
        y = total_h - mh  # bottom align
        canvas.paste(m.convert("RGBA"), (x, y), m.convert("RGBA"))
        x += mw + gap

    # 4) Devolver PNG
    out = io.BytesIO()
    canvas.save(out, format="PNG", optimize=True)
    out.seek(0)
    return send_file(out, mimetype="image/png")
