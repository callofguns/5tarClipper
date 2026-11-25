"""Draws the app icon (assets/icon.png + assets/icon.ico). Run: python packaging/make_icon.py"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
S = 1024  # draw big, then shrink for smooth edges

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
# pink -> orange diagonal gradient
grad = Image.new("RGBA", (S, S))
px = grad.load()
a, b = (255, 79, 163), (255, 154, 64)
for y in range(S):
    for x in range(S):
        t = (x + y) / (2 * S)
        px[x, y] = tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3)) + (255,)
mask = Image.new("L", (S, S), 0)
ImageDraw.Draw(mask).rounded_rectangle([40, 40, S - 40, S - 40], radius=220, fill=255)
img.paste(grad, (0, 0), mask)

d = ImageDraw.Draw(img)
# a vertical "phone" frame with a play triangle: vertical clips
d.rounded_rectangle([330, 190, 694, 834], radius=70, outline=(255, 255, 255, 255), width=56)
d.polygon([(450, 380), (450, 644), (640, 512)], fill=(255, 255, 255, 255))

assets = ROOT / "assets"
assets.mkdir(exist_ok=True)
small = img.resize((256, 256), Image.LANCZOS)
small.save(assets / "icon.png")
img.save(assets / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("saved", assets / "icon.png", assets / "icon.ico")
