from flask import Flask, request, send_file, jsonify
from PIL import Image
import requests
from io import BytesIO
import math

app = Flask(__name__)

@app.route("/", methods=["GET"])
def health():
    return "OK", 200


def _color_dist(c1, c2):
    return math.sqrt(
        (c1[0] - c2[0]) ** 2 +
        (c1[1] - c2[1]) ** 2 +
        (c1[2] - c2[2]) ** 2
    )


def trim_by_bg_color(img: Image.Image, tol: int = 35, pad: int = 0) -> Image.Image:
    """
    Recorta el borde detectando el color de fondo por las esquinas.
    tol: tolerancia de color (más alto = recorta más “agresivo”)
    pad: padding extra alrededor del recorte
    """
    im = img.convert("RGBA")
    w, h = im.size
    px = im.load()

    # Tomar muestras de fondo en esquinas (evita pixel raro puntual)
    corners = [
        px[0, 0], px[w-1, 0], px[0, h-1], px[w-1, h-1]
    ]
    # Convertir a RGB
    corners_rgb = [(c[0], c[1], c[2]) for c in corners]

    # Elegir el “fondo” como el color más repetido / más cercano al promedio
    avg = (
        sum(c[0] for c in corners_rgb) // 4,
        sum(c[1] for c in corners_rgb) // 4,
        sum(c[2] for c in corners_rgb) // 4,
    )

    # bbox del contenido (pixeles que NO son fondo)
    minx, miny = w, h
    maxx, maxy = -1, -1

    for y in range(h):
        for x in range(w):
            r, g, b, a = px[x, y]
            if a == 0:
                # Transparente => asumimos fondo
                continue

            d = _color_dist((r, g, b), avg)
            if d > tol:
                if x < minx: minx = x
                if y < miny: miny = y
                if x > maxx: maxx = x
                if y > maxy: maxy = y

    # Si no detecta nada, devolver igual
    if maxx < minx or maxy < miny:
        return img.convert("RGB")

    # Aplicar padding
    minx = max(minx - pad, 0)
    miny = max(miny - pad, 0)
    maxx = min(maxx + pad, w - 1)
    maxy = min(maxy + pad, h - 1)

    cropped = im.crop((minx, miny, maxx + 1, maxy + 1))
    return cropped.convert("RGB")


@app.route("/render", methods=["POST"])
def render():
    data = request.get_json(silent=True) or {}
    urls = data.get("urls")

    if not urls or not isinstance(urls, list):
        return jsonify({"error": "urls must be a list"}), 400

    # Ajustables desde n8n
    tol = int(data.get("tol", 35))   # probá 35, 45, 60
    pad = int(data.get("pad", 0))    # si querés un margen mínimo, poné 2-5

    images = []
    for u in urls:
        try:
            print("Bajando:", u, flush=True)
            r = requests.get(u, timeout=30)
            r.raise_for_status()
            img = Image.open(BytesIO(r.content))

            # ✅ recorte robusto por “fondo de esquina”
            img = trim_by_bg_color(img, tol=tol, pad=pad)

            images.append(img)
        except Exception as e:
            print("Error procesando:", u, e, flush=True)
            return jsonify({"error": f"Error processing {u}", "detail": str(e)}), 500

    # canvas final
    total_width = sum(im.width for im in images)
    max_height = max(im.height for im in images)

    canvas = Image.new("RGB", (total_width, max_height), (255, 255, 255))

    # pegar sin espacios, alineado abajo
    x = 0
    for im in images:
        y = max_height - im.height
        canvas.paste(im, (x, y))
        x += im.width

    out = BytesIO()
    canvas.save(out, format="PNG")
    out.seek(0)
    return send_file(out, mimetype="image/png", as_attachment=False, download_name="render.png")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
