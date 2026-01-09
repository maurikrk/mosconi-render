import io
import time
import traceback
from typing import List

import requests
from flask import Flask, request, jsonify, send_file
from PIL import Image

app = Flask(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Mosconi-Renderer/alpha-threshold)",
    "Accept": "image/*,*/*;q=0.8",
}

TIMEOUT = 30
RETRIES = 2

# 🔥 CLAVE: umbral de alpha
# todo pixel con alpha <= ALPHA_CUTOFF se trata como transparente real
ALPHA_CUTOFF = 20   # probá 10 / 20 / 35 si tus bordes son suaves
PADDING = 2         # deja 1-3px de margen final


def download_rgba(url: str) -> Image.Image:
    last = None
    for i in range(RETRIES + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            img = Image.open(io.BytesIO(r.content)).convert("RGBA")
            img.load()
            return img
        except Exception as e:
            last = e
            time.sleep(0.5 * (i + 1))
    raise last


def trim_alpha_threshold(img: Image.Image, cutoff: int = ALPHA_CUTOFF, padding: int = PADDING) -> Image.Image:
    """
    Recorta el canvas usando alpha con umbral:
    - alpha <= cutoff => transparente
    - alpha > cutoff  => contenido
    """
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    alpha = img.split()[-1]

    # Binarizamos alpha con umbral
    # 0 para transparente, 255 para contenido
    a = alpha.point(lambda p: 255 if p > cutoff else 0)

    bbox = a.getbbox()
    if not bbox:
        return img

    left, top, right, bottom = bbox
    left = max(0, left - padding)
    top = max(0, top - padding)
    right = min(img.width, right + padding)
    bottom = min(img.height, bottom + padding)

    return img.crop((left, top, right, bottom))


def resize_to_min_height(images: List[Image.Image]) -> List[Image.Image]:
    min_h = min(im.height for im in images)
    out = []
    for im in images:
        if im.height != min_h:
            ratio = min_h / im.height
            new_w = max(1, int(im.width * ratio))
            im = im.resize((new_w, min_h), Image.LANCZOS)
        out.append(im)
    return out


@app.get("/health")
def health():
    return jsonify({"ok": True}), 200


@app.post("/render")
def render():
    try:
        data = request.get_json(force=True)
        urls = data.get("urls")

        if not isinstance(urls, list) or len(urls) == 0:
            return jsonify({"ok": False, "error": "Body inválido. Esperado: { urls: [...] }"}), 400

        imgs = []
        for u in urls:
            img = download_rgba(u)
            img = trim_alpha_threshold(img)   # ✅ ACÁ está la diferencia real
            imgs.append(img)

        imgs = resize_to_min_height(imgs)

        total_w = sum(im.width for im in imgs)
        h = imgs[0].height

        # fondo transparente (si querés blanco, lo cambiamos)
        canvas = Image.new("RGBA", (total_w, h), (0, 0, 0, 0))

        x = 0
        for im in imgs:
            canvas.alpha_composite(im, (x, 0))
            x += im.width

        buf = io.BytesIO()
        canvas.save(buf, format="PNG", optimize=True)
        buf.seek(0)

        return send_file(buf, mimetype="image/png")

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
            "trace": traceback.format_exc()[:1500]
        }), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
