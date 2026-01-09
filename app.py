import io
import requests
from flask import Flask, request, send_file, jsonify
from PIL import Image

app = Flask(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Mosconi-Renderer)",
    "Accept": "image/*,*/*;q=0.8",
}

def download_image(url):
    r = requests.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return Image.open(io.BytesIO(r.content)).convert("RGBA")

def trim_by_alpha(img: Image.Image) -> Image.Image:
    """
    Recorta el canvas usando el canal alpha (transparencia real)
    """
    alpha = img.split()[-1]
    bbox = alpha.getbbox()
    if bbox:
        return img.crop(bbox)
    return img

def resize_to_same_height(images):
    min_h = min(img.height for img in images)
    out = []
    for img in images:
        if img.height != min_h:
            ratio = min_h / img.height
            new_w = int(img.width * ratio)
            img = img.resize((new_w, min_h), Image.LANCZOS)
        out.append(img)
    return out

@app.post("/render")
def render():
    data = request.get_json()
    urls = data.get("urls")

    if not urls:
        return jsonify({"error": "Falta urls"}), 400

    images = []
    for url in urls:
        img = download_image(url)
        img = trim_by_alpha(img)     # 🔴 CLAVE
        images.append(img)

    images = resize_to_same_height(images)

    total_width = sum(img.width for img in images)
    height = images[0].height

    canvas = Image.new("RGBA", (total_width, height), (0, 0, 0, 0))

    x = 0
    for img in images:
        canvas.paste(img, (x, 0), img)
        x += img.width

    output = io.BytesIO()
    canvas.save(output, format="PNG")
    output.seek(0)

    return send_file(output, mimetype="image/png")
