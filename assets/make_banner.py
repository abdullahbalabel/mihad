"""Draw the README banner (assets/banner.png). Run: python assets/make_banner.py (needs Pillow)."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

W, H, S = 1280, 400, 2
OUT = Path(__file__).resolve().parent / "banner.png"
FONTS = "C:/Windows/Fonts/"


def font(name, size):
    return ImageFont.truetype(FONTS + name, size * S)


img = Image.new("RGB", (W * S, H * S))
d = ImageDraw.Draw(img)
top, bottom = (14, 22, 38), (16, 64, 78)
for y in range(H * S):
    t = y / (H * S)
    d.line([(0, y), (W * S, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))

# soft glow behind the mark
glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
gd = ImageDraw.Draw(glow)
gd.ellipse((60 * S, 40 * S, 420 * S, 400 * S), fill=(56, 189, 176, 70))
glow = glow.filter(ImageFilter.GaussianBlur(70 * S))
img.paste(glow, (0, 0), glow)
d = ImageDraw.Draw(img)

# mark: concentric arcs (memory that grows) with a check (verified)
cx, cy = 240 * S, 200 * S
for r, w, alpha in ((118, 6, 255), (88, 6, 200), (58, 6, 150)):
    color = (94, 234, 212) if r == 118 else (45, 212, 191) if r == 88 else (20, 184, 166)
    d.arc((cx - r * S, cy - r * S, cx + r * S, cy + r * S), start=200, end=520 - r, fill=color, width=w * S)
d.line([(cx - 28 * S, cy + 2 * S), (cx - 6 * S, cy + 24 * S), (cx + 34 * S, cy - 22 * S)],
       fill=(240, 253, 250), width=12 * S, joint="curve")

x = 420 * S
d.text((x, 92 * S), "MIHAD", font=font("segoeuib.ttf", 92), fill=(240, 253, 250))
d.text((x + 4 * S, 210 * S), "Developmental Memory for Coding Agents", font=font("seguisb.ttf", 30),
       fill=(153, 246, 228))
d.text((x + 4 * S, 256 * S), "Adopts only what passes independent checks.  Learns from experience.",
       font=font("segoeui.ttf", 22), fill=(204, 223, 230))

chips = ["OMP", "Claude Code", "Codex", "10 languages", "MCP"]
cxp = x + 4 * S
for c in chips:
    f = font("seguisb.ttf", 17)
    tw = d.textlength(c, font=f)
    box = (cxp, 312 * S, cxp + tw + 30 * S, 346 * S)
    d.rounded_rectangle(box, radius=17 * S, fill=(15, 118, 110), outline=(45, 212, 191), width=2 * S)
    d.text((cxp + 15 * S, 316 * S), c, font=f, fill=(240, 253, 250))
    cxp = box[2] + 12 * S

img = img.resize((W, H), Image.LANCZOS)
img.save(OUT, optimize=True)
print("wrote", OUT, img.size)
