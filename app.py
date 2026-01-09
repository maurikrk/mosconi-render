from flask import Flask, request, send_file, jsonify
import requests
from PIL import Image
from io import BytesIO
import os

app = Flask(__name__)

@app.get("/")
def health():
    return "ok"

@app.post("/render")
def render():
    data = request.get_json(force=True)
    urls = data.get("urls", [])

    if not urls:
        return jsonify({"error": "urls vacío"}), 400

    images = []
    for url in urls:
        headers = {"User-Agent": "mosconi-render/1.0"}
        r = requests.get(url, timeout=(5, 25), headers=headers)
        r.raise_for_status()
        images.append(Image.open(BytesIO(r.content)).convert("RGBA"))

    total_width = sum(img.width for img in images)
    max_height = max(img.height for img in images)

    canvas = Image.new("RGBA", (total_width, max_height), (0, 0, 0, 0))

    x = 0
    for img in images:
        y = max_height - img.height
        canvas.paste(img, (x, y), img)
        x += img.width

    output = BytesIO()
    canvas.save(output, format="PNG")
    output.seek(0)

    return send_file(
        output,
        mimetype="image/png",
        download_name="render.png"
    )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
