"""Draws the slate the stream shows while Chrome (re)starts: slate.py OUT.png WIDTHxHEIGHT."""
import sys

from PIL import Image, ImageDraw, ImageFont

w, h = (int(x) for x in sys.argv[2].split("x"))
img = Image.new("RGB", (w, h), (11, 18, 32))
d = ImageDraw.Draw(img)
big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", h // 14)
small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", h // 36)
for text, font, y in (("AirOpsEnv live", big, 0.42), ("Back in a moment", small, 0.56)):
    box = d.textbbox((0, 0), text, font=font)
    d.text(((w - box[2]) / 2, h * y), text, font=font, fill=(226, 232, 240))
img.save(sys.argv[1])
