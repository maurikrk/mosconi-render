import io
import os
import time
import traceback
from typing import List, Tuple, Optional

import requests
from flask import Flask, request, jsonify, send_file
from PIL import Image, ImageChops, ImageOps

app = Flask(__name__)

# --- Config ---
MAX_IMAGES = int(os.getenv("MAX_IMAGES", "6"))
TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "30"))
RETRIES = int(os.getenv("HTTP_RETRIES", "2"))
RETRY_BACKOFF = float(os.getenv("RETRY_BACKOFF", "0.6"))

# Para evitar imágenes gigantes por error
MAX_SIDE = int(os.getenv("MAX_SIDE", "2500"))

# Trim settings (clave para que no queden separadas)
TRIM_BG = (255, 255, 255)                 # fondo blanco
TRIM_TOLERANCE = int(os.getenv("TRIM_TOLERANCE", "18"))  # 10-25 suele andar bien
TRIM_PADDING = int(os.getenv("TRIM_PADDING", "2"))       # deja 1-3px de margen

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Mosconi-Renderer/1.0)",
    "Accept": "image/*,*/*;q=0.8",
}


def _fail(status: int, message: str, extra: Optional[dict] = None):
    payload = {"ok": False, "error": message}
    if extra:
        payload.update(extra)
    return jsonify(payload), status


def download_bytes(url: str) -> Tuple[Optional[bytes], Optional[str], Optional[int]]:
    last_err = None
    last_status = None

    for attempt in range(RETRIES + 1):
        try:
            r = requests.get(url, headers=DEFAULT_HEADERS, timeout=TIMEOUT)
            last_status = r.status_code

            if r.status_code >= 400:
                snippet = (r.text or "")[:200]
                return None, f"HTTP {r.status_code} al bajar imagen. Snippet: {snippet}", r.status_code

            return r.content, None, r.status_code

        except Exception as e:
            last_err = str(e)
            time.sleep(RETRY_BACKOFF * (attempt + 1))

    return None, f"Error al bajar imagen luego de reintentos: {last_err}", last_status


def open_image(content: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(content))
    img.load()
    # Normalizamos a RGB (tus PNG son sobre blanco, y el trim lo hacemos por blanco)
    if img.mode != "RGB":
        img = img.convert("RGB")
    return img


def clamp_image(img: Image.Image) -> Image.Image:
    w, h = img.size
    m = max(w, h)
    if m <= MAX_SIDE:
        return img
    scale = MAX_SIDE / float(m)
    nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
    return img.resize((nw, nh), Image.LANCZOS)


def trim_white(img: Image.Image, bg=TRIM_BG, tolerance=TRIM_TOLERANCE, padding=TRIM_PADDING) -> Image.Image:
    """
    Recorta bordes blancos (y casi blancos) manteniendo sombras suaves.
    - tolerance: cuanto más alto, más agresivo recorta (10-25 recomendado).
    - padding: vuelve a agregar unos px alrededor para que no quede "comido".
    """
    if img.mode != "RGB":
        img = img.convert("RGB")

    bg_img = Image.new("RGB", img.size, bg)
    diff = ImageChops.difference(img, bg_img)

    # Convertimos diff a escala de grises para medir "distancia al blanco"
    diff_gray = diff.convert("L")

    # Umbral: todo lo que sea muy parecido al blanco queda negro (fondo), lo demás blanco (contenido)
    # Invertimos para que el contenido quede blanco y el fondo negro
    # (Imagen binaria para bbox)
    bw = diff_gray.point(lambda x: 255 if x > tolerance else 0)

    bbox = bw.getbbox()
    if not bbox:
        # Si no encontramos nada, devolvemos igual
        return img

    left, top, right, bottom = bbox

    # Aplicamos padding con límites
    left = max(0, left - padding)
    top = max(0, top - padding)
    right = min(img.size[0], right + padding)
    bottom = min(img.size[1], bottom + padding)

    return img.crop((left, top, right, bottom))


def resize_to_height(img: Image.Image, target_h: int) -> Image.Image:
    w, h = img.size
    if h == target_h:
        return img
    scale = target_h / float(h)
    new_w = max(1, int(w * scale))
    return img.resize((new_w, target_h), Image.LANCZOS)


def composite_horizontal(images: List[Image.Image], background=(255, 255, 255)) -> Image.Image:
    """
    Une imágenes sin espacios, mismo alto, fondo blanco.
    """
    heights = [im.size[1] for im in images]
    target_h = min(heights)  # evitamos upscaling

    resized = [resize_to_height(im, target_h) for im in images]
    total_w = sum(im.size[0] for im in resized)

    out = Image.new("RGB", (total_w, target_h), background)

    x = 0
    for im in resized:
        out.paste(im, (x, 0))
        x += im.size[0]

    return out


@app.get("/health")
def health():
    return jsonify({"ok": True}), 200


@app.post("/render")
def render():
    try:
        data = request.get_json(force=True, silent=False)
        if not isinstance(data, dict):
            return _fail(400, "Body inválido. Esperado JSON object {idcoti, urls:[...]}")

        idcoti = data.get("idcoti")
        urls = data.get("urls")

        if idcoti is None:
            return _fail(400, "Falta 'idcoti' en el body.")
        if not isinstance(urls, list) or len(urls) == 0:
            return _fail(400, "Falta 'urls' o no es una lista no vacía.", {"urls": urls})
        if len(urls) > MAX_IMAGES:
            return _fail(400, f"Demasiadas imágenes. Máximo {MAX_IMAGES}.", {"count": len(urls)})

        for i, u in enumerate(urls):
            if not isinstance(u, str) or not (u.startswith("http://") or u.startswith("https://")):
                return _fail(400, f"URL inválida en urls[{i}]: {u}")

        images: List[Image.Image] = []
        for i, url in enumerate(urls):
            content, err, status = download_bytes(url)
            if err:
                return _fail(400, f"No pude bajar urls[{i}]. {err}", {"index": i, "url": url, "status": status})

            try:
                img = open_image(content)
                img = clamp_image(img)

                # ✅ CLAVE: recortamos blanco antes de unir (esto elimina los "espacios")
                img = trim_white(img)

                images.append(img)
            except Exception as e:
                return _fail(400, f"Imagen inválida en urls[{i}]. Error: {str(e)}", {"index": i, "url": url})

        result = composite_horizontal(images, background=(255, 255, 255))

        buf = io.BytesIO()
        result.save(buf, format="PNG", optimize=True)
        buf.seek(0)

        filename = f"render_{idcoti}.png"
        return send_file(
            buf,
            mimetype="image/png",
            as_attachment=False,
            download_name=filename
        )

    except Exception as e:
        return jsonify({
            "ok": False,
            "error": str(e),
            "trace": traceback.format_exc()[:2000]
        }), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
