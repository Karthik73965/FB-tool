"""Draws the app icon (a vertical clip with a caption bar) -> packaging/icon.ico (Windows) and icon.icns (macOS)."""
import os
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)
d.rounded_rectangle([40, 40, S - 40, S - 40], radius=220, fill=(18, 20, 23, 255))
d.rounded_rectangle([330, 150, 694, 874], radius=56, fill=(42, 47, 56, 255), outline=(255, 230, 0, 255), width=22)
d.polygon([(450, 380), (450, 600), (630, 490)], fill=(255, 255, 255, 255))           # play
d.rounded_rectangle([380, 690, 644, 750], radius=20, fill=(255, 230, 0, 255))      # caption bar
img.save(os.path.join(HERE, "icon.ico"), sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
img.save(os.path.join(HERE, "icon.icns"))
img.save(os.path.join(HERE, "icon.png"))
print("icons written")
