from flask import Flask, request, send_file, jsonify
from PIL import Image, ImageChops
import requests
from io import BytesIO

app = Flask(__name__)

@app.route("/", methods=["GET"])
def health():
    return "OK", 200


def trim_white(img: Image.Image, threshold: int = 245) -> Image.Image:
    """
    Recorta márgenes casi-blancos.
    threshold: 0-255. Más alto = recorta menos; más bajo = recorta más.
    """
    # Trabajamos en RGB
    im = img.convert("RGB")

    # Creamos una máscara: píxeles "no blancos" (donde haya algo)
    # Consideramos blanco si R,G,B >= threshold
    # Entonces "contenido" = donde alguno < threshold
    r, g, b = im.split()
    mask = ImageChops.darker(r, g)
    mask = ImageChops.darker(mask, b)
    # mask es más oscura donde hay contenido; blanquecina donde es blanco
    # Convertimos a binaria según threshold
    mask = mask.point(lambda p: 255 if p < threshold else 0)

    bbox = mask.getbbox()
    if bbox:
        return im.crop(bbox)
    return im  # si no detecta contenido, devuelve igual


@app.route("/render", methods=["POST"])
def render():
    data = request.get_json()
    if not data:
        return jsonify({"error": "No JSON body received"}), 400

    urls = data.get("urls")
    if not urls or not isinstance(urls, list):
        return jsonify({"error": "urls must be a list"}), 400

    # Umbral opcional desde el body
    threshold = int(data.get("threshold", 245))

    images = []
    for u in urls:
        try:
            print("Bajando:", u, flush=True)
            r = requests.get(u, timeout=20)
            r.raise_for_status()
            img = Image.open(BytesIO(r.content)).convert("RGB")

            # ✅ Recortar márgenes blancos
            img = trim_white(img, threshold=threshold)

            images.append(img)
        except Exception as e:
            print("Error bajando/recortando:", u, e, flush=True)
            return jsonify({"error": f"Error downloading/processing {u}"}), 500

    if not images:
        return jsonify({"error": "No images downloaded"}), 400

    # Calcular tamaño final
    total_width = sum(img.width for img in images)
    max_height = max(img.height for img in images)

    # Canvas blanco
    canvas = Image.new("RGB", (total_width, max_height), (255, 255, 255))

    # Pegar sin espacios, alineado abajo
    x = 0
    for img in images:
        y = max_height - img.height
        canvas.paste(img, (x, y))
        x += img.width

    out = BytesIO()
    canvas.save(out, format="PNG")
    out.seek(0)

    return send_file(out, mimetype="image/png", as_attachment=False, download_name="render.png")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
