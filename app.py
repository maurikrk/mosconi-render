import io
import time
import traceback
from typing import List

import requests
from flask import Flask, request, jsonify, send_file
from PIL import Image, ImageDraw

app = Flask(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Mosconi-Renderer)",
    "Accept": "image/*,*/*;q=0.8",
}

TIMEOUT = 30
RETRIES = 2

ALPHA_CUTOFF = 20
PADDING = 0

# 👇 Ajustes de “unión real”
OVERLAP = 0            # dejalo en 0 si usás SEAM_CROP
SEAM_CROP = 12         # 🔥 probá 12 / 18 / 24 (recorta cantos internos)

VERSION = f"vSEAM{SEAM_CROP}-OV{OVERLAP}-A{ALPHA_CUTOFF}"


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
    alpha = img.split()[-1]
    mask = alpha.point(lambda p: 255 if p > cutoff else 0)
    bbox = mask.getbbox()
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


def crop_internal_sides(images: List[Image.Image], seam_crop: int) -> List[Image.Image]:
    """
    Recorta laterales internos para que parezcan módulos pegados (placard real).
    - Primero: recorta derecha
    - Medio(s): recorta izquierda y derecha
    - Último: recorta izquierda
    """
    if seam_crop <= 0 or len(images) <= 1:
        return images

    out = []
    n = len(images)
    for i, im in enumerate(images):
        w, h = im.size
        sc = min(seam_crop, (w // 4))  # seguridad: no cortar demasiado

        left = 0
        right = w

        if i == 0:
            # primero: recorta derecha
            right = w - sc
        elif i == n - 1:
            # último: recorta izquierda
            left = sc
        else:
            # medio: recorta ambos lados
            left = sc
            right = w - sc

        if right <= left + 2:
            out.append(im)
        else:
            out.append(im.crop((left, 0, right, h)))

    return out


@app.get("/health")
def health():
    return jsonify({"ok": True, "version": VERSION}), 200


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
            img = trim_alpha_threshold(img)
            imgs.append(img)

        imgs = resize_to_min_height(imgs)

        # 🔥 Esto es lo que “pega” de verdad visualmente
        imgs = crop_internal_sides(imgs, SEAM_CROP)

        # (Opcional) overlap, normalmente 0 si usás SEAM_CROP
        safe_overlap = 0
        if OVERLAP > 0 and len(imgs) > 1:
            safe_overlap = min(OVERLAP, min(im.width for im in imgs) - 1)

        total_w = sum(im.width for im in imgs) - safe_overlap * (len(imgs) - 1)
        h = imgs[0].height

        canvas = Image.new("RGBA", (total_w, h), (0, 0, 0, 0))

        x = 0
        for im in imgs:
            canvas.alpha_composite(im, (x, 0))
            x += im.width - safe_overlap

        # ✅ Firma: si no la ves, NO está deployado el código nuevo
        draw = ImageDraw.Draw(canvas)
        draw.text((10, 10), VERSION, fill=(255, 0, 0, 255))

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
