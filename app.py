import io
import time
import traceback
from typing import List

import requests
from flask import Flask, request, jsonify, send_file
from PIL import Image, ImageDraw, ImageFilter

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
SEAM_CROP = 12         # probá 12 / 18 / 24 (recorta cantos internos)

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
            right = w - sc
        elif i == n - 1:
            left = sc
        else:
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


# ✅ SOMBRA MEJORADA (NO cambia el tamaño del canvas)
def add_bottom_shadow(img: Image.Image,
                      shadow_height: int = 50,
                      blur_radius: int = 28,
                      opacity: int = 70,
                      y_offset: int = -10) -> Image.Image:
    """
    Sombra suave debajo del mueble, sin agrandar el canvas.
    y_offset negativo la sube un poco (queda más realista).
    """
    w, h = img.size

    shadow = Image.new("RGBA", (w, shadow_height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(shadow)

    # elipse ancha y baja
    draw.ellipse(
        (-w * 0.06, shadow_height * 0.10,
         w * 1.06, shadow_height * 1.60),
        fill=(0, 0, 0, opacity)
    )

    shadow = shadow.filter(ImageFilter.GaussianBlur(blur_radius))

    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(img, (0, 0), img)

    # pegamos sombra cerca de la base del mueble
    out.paste(shadow, (0, h - shadow_height + y_offset), shadow)

    return out


# ✅ BASE COMÚN (zócalo/piso) que sí agrega altura
def add_base(img: Image.Image,
             base_height: int = 28,
             side_margin: int = 22,
             color=(235, 235, 235, 255),
             top_line_color=(215, 215, 215, 255),
             top_line_thickness: int = 2,
             radius: int = 10) -> Image.Image:
    """
    Agrega una base común debajo del render.
    """
    w, h = img.size
    new_h = h + base_height

    out = Image.new("RGBA", (w, new_h), (0, 0, 0, 0))
    out.paste(img, (0, 0), img)

    draw = ImageDraw.Draw(out)

    x0 = side_margin
    x1 = w - side_margin
    y0 = h
    y1 = h + base_height

    if radius > 0:
        draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=color)
    else:
        draw.rectangle([x0, y0, x1, y1], fill=color)

    # línea superior (relieve)
    for t in range(top_line_thickness):
        draw.line([(x0, y0 + t), (x1, y0 + t)], fill=top_line_color)

    return out


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

        # 🔥 Pegado real (cantos internos)
        imgs = crop_internal_sides(imgs, SEAM_CROP)

        # Overlap opcional (normalmente 0 si usás SEAM_CROP)
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

        # ✅ 1) Sombra común (sin cambiar tamaño)
        canvas = add_bottom_shadow(canvas, shadow_height=50, blur_radius=28, opacity=70, y_offset=-10)

        # ✅ 2) Base común (agrega altura)
        canvas = add_base(canvas, base_height=28, side_margin=22, radius=10)

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

