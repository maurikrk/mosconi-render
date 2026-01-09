from flask import Flask, request, send_file, jsonify
from PIL import Image
import requests
from io import BytesIO

app = Flask(__name__)

@app.route("/", methods=["GET"])
def health():
    return "OK", 200


@app.route("/render", methods=["POST"])
def render():
    data = request.get_json()

    if not data:
        return jsonify({"error": "No JSON body received"}), 400

    urls = data.get("urls")

    if not urls or not isinstance(urls, list):
        return jsonify({"error": "urls must be a list"}), 400

    images = []

    # 1️⃣ Descargar imágenes
    for u in urls:
        try:
            print("Bajando:", u, flush=True)
            r = requests.get(u, timeout=20)
            r.raise_for_status()
            img = Image.open(BytesIO(r.content)).convert("RGB")
            images.append(img)
        except Exception as e:
            print("Error bajando imagen:", u, e, flush=True)
            return jsonify({"error": f"Error downloading {u}"}), 500

    if not images:
        return jsonify({"error": "No images downloaded"}), 400

    # 2️⃣ Calcular tamaño final
    widths = [img.width for img in images]
    heights = [img.height for img in images]

    total_width = sum(widths)
    max_height = max(heights)

    # 3️⃣ Crear canvas blanco
    canvas = Image.new("RGB", (total_width, max_height), (255, 255, 255))

    # 4️⃣ Pegar imágenes (izquierda → derecha, alineadas abajo)
    x_offset = 0
    for img in images:
        y_offset = max_height - img.height  # bottom align
        canvas.paste(img, (x_offset, y_offset))
        x_offset += img.width

    # 5️⃣ Devolver imagen final
    output = BytesIO()
    canvas.save(output, format="PNG")
    output.seek(0)

    return send_file(
        output,
        mimetype="image/png",
        as_attachment=False,
        download_name="render.png"
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
