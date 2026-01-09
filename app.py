import io
import os
import time
import traceback
from typing import List, Tuple, Optional

import requests
from flask import Flask, request, jsonify, send_file
from PIL import Image

app = Flask(__name__)

# --- Config ---
MAX_IMAGES = int(os.getenv("MAX_IMAGES", "6"))
TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "30"))          # seconds
RETRIES = int(os.getenv("HTTP_RETRIES", "2"))           # retry count after first attempt
RETRY_BACKOFF = float(os.getenv("RETRY_BACKOFF", "0.6"))# seconds (multiplied by attempt)
MAX_SIDE = int(os.getenv("MAX_SIDE", "2000"))           # prevent giant images (basic safety)

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
    """
    Returns: (content_bytes, error_message, http_status)
    """
    last_err = None
    last_status = None

    for attempt in range(RETRIES + 1):
        try:
            r = requests.get(url, headers=DEFAULT_HEADERS, timeout=TIMEOUT)
            last_status = r.status_code
            if r.status_code >= 400:
                # Sometimes servers return HTML; include snippet to help debug
                snippet = (r.text or "")[:200]
                return None, f"HTTP {r.status_code} al bajar imagen. Snippet: {snippet}", r.status_code

            content_type = (r.headers.get("Content-Type") or "").lower()
            if "image" not in content_type and not url.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                return None, f"Respuesta no parece imagen. Content-Type={content_type}", r.status_code

            return r.content, None, r.status_code

        except Exception as e:
            last_err = str(e)
            time.sleep(RETRY_BACKOFF * (attempt + 1))

    return None, f"Error al bajar imagen luego de reintentos: {last_err}", last_status


def open_image(content: bytes) -> Image.Image:
    img = Image.open(io.BytesIO(content))
    img.load()
    # Convert to RGBA to handle transparency consistently
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    return img


def clamp_image(img: Image.Image) -> Image.Image:
    """
    Prevent absurdly large images (basic protection).
    Keeps aspect ratio, clamps max side to MAX_SIDE.
    """
    w, h = img.size
    max_side = max(w, h)
    if max_side <= MAX_SIDE:
        return img

    scale = MAX_SIDE / float(max_side)
    new_w = max(1, int(w * scale))
    new_h = max(1, int(h * scale))
    return img.resize((new_w, new_h), Image.LANCZOS)


def resize_to_height(img: Image.Image, target_h: int) -> Image.Image:
    w, h = img.size
    if h == target_h:
        return img
    scale = target_h / float(h)
    new_w = max(1, int(w * scale))
    return img.resize((new_w, target_h), Image.LANCZOS)


def composite_horizontal(images: List[Image.Image], background=(255, 255, 255)) -> Image.Image:
    """
    Create one horizontal image, same height, background white, alpha composited.
    """
    # Normalize height to the minimum height (prevents upscaling artifacts)
    heights = [im.size[1] for im in images]
    target_h = min(heights)

    resized = [resize_to_height(im, target_h) for im in images]
    total_w = sum(im.size[0] for im in resized)

    canvas = Image.new("RGBA", (total_w, target_h), background + (255,))

    x = 0
    for im in resized:
        canvas.alpha_composite(im, (x, 0))
        x += im.size[0]

    # Convert to RGB for final PNG with solid white background
    out = Image.new("RGB", canvas.size, background)
    out.paste(canvas, mask=canvas.split()[3])  # use alpha as mask
    return out


@app.get("/health")
def health():
    return jsonify({"ok": True}), 200


@app.post("/render")
def render():
    try:
        data = request.get_json(force=True, silent=False)
        if not isinstance(data, dict):
            return _fail(400, "Body inválido. Esperado JSON object {idcoti, urls:[...]}", {"data": data})

        idcoti = data.get("idcoti")
        urls = data.get("urls")

        if idcoti is None:
            return _fail(400, "Falta 'idcoti' en el body.")
        if not isinstance(urls, list) or len(urls) == 0:
            return _fail(400, "Falta 'urls' o no es una lista no vacía.", {"urls": urls})
        if len(urls) > MAX_IMAGES:
            return _fail(400, f"Demasiadas imágenes. Máximo {MAX_IMAGES}.", {"count": len(urls)})

        # Validate each url
        for i, u in enumerate(urls):
            if not isinstance(u, str) or not (u.startswith("http://") or u.startswith("https://")):
                return _fail(400, f"URL inválida en urls[{i}]: {u}")

        images: List[Image.Image] = []
        for i, url in enumerate(urls):
            content, err, status = download_bytes(url)
            if err:
                return _fail(
                    400,
                    f"No pude bajar urls[{i}]. {err}",
                    {"index": i, "url": url, "status": status}
                )

            try:
                img = open_image(content)
                img = clamp_image(img)
                images.append(img)
            except Exception as e:
                return _fail(
                    400,
                    f"Imagen inválida en urls[{i}]. Error PIL: {str(e)}",
                    {"index": i, "url": url}
                )

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
        # Always return JSON with trace to debug (n8n will show it)
        return jsonify({
            "ok": False,
            "error": str(e),
            "trace": traceback.format_exc()[:2000]
        }), 500


if __name__ == "__main__":
    # Local run: python app.py
    port = int(os.getenv("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)
