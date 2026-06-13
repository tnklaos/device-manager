"""Generate app icons (icon.png / icon.icns / icon.ico) for Device Manager."""
import os
import subprocess
from PIL import Image, ImageDraw

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "build_assets")
os.makedirs(ASSETS, exist_ok=True)

S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# rounded background (indigo gradient-ish: solid indigo)
pad = 80
d.rounded_rectangle([pad, pad, S - pad, S - pad], radius=200, fill=(99, 102, 241, 255))

# a phone outline
pw, ph = 320, 560
px, py = (S - pw) // 2, (S - ph) // 2 - 20
d.rounded_rectangle([px, py, px + pw, py + ph], radius=48, outline=(255, 255, 255, 255),
                    width=26)
# screen line + home dot
d.line([px + 70, py + 70, px + pw - 70, py + 70], fill=(255, 255, 255, 200), width=14)
d.ellipse([S // 2 - 22, py + ph - 78, S // 2 + 22, py + ph - 34], fill=(255, 255, 255, 230))

# three "signal" dots (devices) top-right
cx, cy = S - 250, 250
for i, r in enumerate((18, 30, 44)):
    d.arc([cx - r, cy - r, cx + r, cy + r], start=200, end=340,
          fill=(255, 255, 255, 230), width=14)

png = os.path.join(ASSETS, "icon.png")
img.save(png)
print("wrote", png)

# .ico (Windows) — multiple sizes
ico = os.path.join(ASSETS, "icon.ico")
img.save(ico, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("wrote", ico)

# .icns (macOS) via iconutil
iconset = os.path.join(ASSETS, "icon.iconset")
os.makedirs(iconset, exist_ok=True)
for sz in (16, 32, 64, 128, 256, 512, 1024):
    img.resize((sz, sz), Image.LANCZOS).save(os.path.join(iconset, f"icon_{sz}x{sz}.png"))
    if sz <= 512:
        img.resize((sz * 2, sz * 2), Image.LANCZOS).save(
            os.path.join(iconset, f"icon_{sz}x{sz}@2x.png"))
try:
    subprocess.run(["iconutil", "-c", "icns", iconset,
                    "-o", os.path.join(ASSETS, "icon.icns")], check=True)
    print("wrote", os.path.join(ASSETS, "icon.icns"))
except Exception as e:
    print("icns skipped (iconutil not available):", e)
