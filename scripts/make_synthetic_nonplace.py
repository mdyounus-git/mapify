#!/usr/bin/env python3
"""Draw images that are obviously not photographs of anywhere.

The case that started this: a cyber-security poster -- a title, some
bullet points, a padlock -- was analysed and came back with map
locations. These are reproductions of that kind of image, drawn from a
fixed seed so the set is identical on every machine and needs no
download: security posters, terminal screenshots, login screens, charts,
slides and flowcharts.

They complement ``fetch_nonplace.py``, whose images are real but come
from wherever Wikimedia's categories happen to lead. Here the content is
controlled, so a failure can be traced to a specific kind of image.

    python scripts/make_synthetic_nonplace.py
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "samples" / "synthetic"

_FONTS = ("C:/Windows/Fonts/arialbd.ttf", "C:/Windows/Fonts/arial.ttf",
          "C:/Windows/Fonts/consola.ttf", "C:/Windows/Fonts/segoeui.ttf",
          "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
          "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")


def font(size: int, mono: bool = False) -> ImageFont.ImageFont:
    order = ([f for f in _FONTS if "consola" in f] + list(_FONTS)) if mono \
        else list(_FONTS)
    for path in order:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


SECURITY_TITLES = (
    "CYBER SECURITY AWARENESS", "PROTECT YOUR DATA", "THINK BEFORE YOU CLICK",
    "STOP PHISHING", "ZERO TRUST SECURITY", "RANSOMWARE: BE PREPARED",
)
SECURITY_BULLETS = (
    "Use strong, unique passwords", "Enable multi-factor authentication",
    "Verify links before clicking", "Keep your software updated",
    "Report suspicious emails", "Encrypt sensitive files",
    "Lock your screen when away", "Back up your data regularly",
)


def padlock(d: ImageDraw.ImageDraw, cx: int, cy: int, s: int, colour) -> None:
    d.rounded_rectangle([cx - s, cy - s // 4, cx + s, cy + s + s // 2],
                        radius=s // 5, fill=colour)
    d.arc([cx - int(s * 0.7), cy - int(s * 1.3), cx + int(s * 0.7),
           cy + int(s * 0.3)], 180, 360, fill=colour, width=max(4, s // 5))
    d.ellipse([cx - s // 6, cy + s // 4, cx + s // 6, cy + s // 4 + s // 3],
              fill=(20, 20, 30))


def shield(d: ImageDraw.ImageDraw, cx: int, cy: int, s: int, colour) -> None:
    d.polygon([(cx, cy - s), (cx + s, cy - s // 2), (cx + int(s * 0.8), cy + s // 2),
               (cx, cy + s), (cx - int(s * 0.8), cy + s // 2),
               (cx - s, cy - s // 2)], fill=colour)


def security_poster(rng: random.Random, i: int) -> Image.Image:
    w, h = rng.choice([(1080, 1350), (1200, 1600), (1240, 1754)])
    bg = rng.choice([(8, 18, 38), (12, 12, 20), (0, 40, 60), (30, 10, 50)])
    accent = rng.choice([(0, 200, 255), (0, 230, 120), (255, 80, 60),
                         (255, 200, 0)])
    img = Image.new("RGB", (w, h), bg)
    d = ImageDraw.Draw(img)
    # Binary-rain backdrop, the stock visual shorthand for "cyber".
    f_bin = font(18, mono=True)
    for _ in range(140):
        x, y = rng.randrange(w), rng.randrange(h)
        shade = tuple(min(255, c // 3 + 10) for c in accent)
        d.text((x, y), "".join(rng.choice("01") for _ in range(8)),
               font=f_bin, fill=shade)
    title = SECURITY_TITLES[i % len(SECURITY_TITLES)]
    f_title = font(int(w * 0.07))
    words = title.split()
    line1 = " ".join(words[: len(words) // 2 + 1])
    line2 = " ".join(words[len(words) // 2 + 1:])
    d.text((w * 0.08, h * 0.08), line1, font=f_title, fill=(255, 255, 255))
    if line2:
        d.text((w * 0.08, h * 0.08 + w * 0.085), line2, font=f_title,
               fill=accent)
    (padlock if i % 2 == 0 else shield)(d, int(w * 0.5), int(h * 0.42),
                                        int(w * 0.13), accent)
    f_b = font(int(w * 0.032))
    for k, text in enumerate(rng.sample(SECURITY_BULLETS, 4)):
        y = h * 0.62 + k * w * 0.06
        d.ellipse([w * 0.08, y + 8, w * 0.08 + 18, y + 26], fill=accent)
        d.text((w * 0.08 + 34, y), text, font=f_b, fill=(230, 235, 240))
    d.text((w * 0.08, h * 0.93), "IT Security Team  |  security@example.org",
           font=font(int(w * 0.022)), fill=(160, 170, 185))
    return img


def terminal(rng: random.Random, i: int) -> Image.Image:
    img = Image.new("RGB", (1920, 1080), (12, 12, 12))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 1920, 36], fill=(45, 45, 48))
    d.text((16, 6), "user@host: ~", font=font(20), fill=(200, 200, 200))
    f = font(22, mono=True)
    cmds = ["nmap -sV 10.0.0.0/24", "sudo tail -f /var/log/auth.log",
            "openssl s_client -connect example.org:443",
            "grep -i 'failed password' /var/log/secure | wc -l",
            "ssh-keygen -t ed25519", "iptables -L -n -v"]
    y = 56
    for _ in range(34):
        if rng.random() < 0.25:
            line = "$ " + rng.choice(cmds)
            colour = (80, 250, 120)
        else:
            line = "".join(rng.choice("abcdef0123456789:. /-") for _ in
                           range(rng.randrange(30, 110)))
            colour = (200, 200, 200)
        d.text((20, y), line, font=f, fill=colour)
        y += 29
    return img


def login_screen(rng: random.Random, i: int) -> Image.Image:
    w, h = rng.choice([(1366, 768), (1440, 900), (1280, 800)])
    top = rng.choice([(40, 80, 160), (20, 120, 110), (90, 40, 140)])
    img = Image.new("RGB", (w, h), (240, 243, 247))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w, h // 3], fill=top)
    cw, ch = 420, 440
    x0, y0 = (w - cw) // 2, h // 5
    d.rounded_rectangle([x0, y0, x0 + cw, y0 + ch], 16, fill=(255, 255, 255),
                        outline=(220, 224, 230))
    d.text((x0 + 40, y0 + 36), "Secure Sign In", font=font(34),
           fill=(30, 35, 45))
    for k, label in enumerate(("Username", "Password")):
        yy = y0 + 120 + k * 100
        d.text((x0 + 40, yy), label, font=font(18), fill=(90, 95, 105))
        d.rounded_rectangle([x0 + 40, yy + 28, x0 + cw - 40, yy + 72], 8,
                            outline=(200, 205, 212), width=2)
    d.rounded_rectangle([x0 + 40, y0 + 340, x0 + cw - 40, y0 + 390], 8,
                        fill=top)
    d.text((x0 + cw // 2 - 36, y0 + 352), "LOG IN", font=font(22),
           fill=(255, 255, 255))
    return img


def bar_chart(rng: random.Random, i: int) -> Image.Image:
    img = Image.new("RGB", (1200, 800), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.text((60, 30), "Security incidents by quarter", font=font(34),
           fill=(30, 30, 30))
    d.line([100, 700, 1140, 700], fill=(60, 60, 60), width=3)
    d.line([100, 120, 100, 700], fill=(60, 60, 60), width=3)
    colours = [(66, 133, 244), (219, 68, 55), (244, 180, 0), (15, 157, 88)]
    for k in range(8):
        v = rng.randrange(80, 540)
        x = 140 + k * 120
        d.rectangle([x, 700 - v, x + 70, 700], fill=colours[k % 4])
        d.text((x + 6, 712), f"Q{k % 4 + 1}", font=font(20), fill=(60, 60, 60))
    return img


def slide(rng: random.Random, i: int) -> Image.Image:
    img = Image.new("RGB", (1920, 1080), (255, 255, 255))
    d = ImageDraw.Draw(img)
    band = rng.choice([(0, 70, 140), (180, 30, 45), (20, 20, 20)])
    d.rectangle([0, 0, 1920, 170], fill=band)
    d.text((80, 50), rng.choice(["Incident Response Plan", "Threat Model",
                                 "Security Roadmap 2026"]),
           font=font(64), fill=(255, 255, 255))
    for k, text in enumerate(rng.sample(SECURITY_BULLETS, 5)):
        d.text((120, 260 + k * 130), "\u2022  " + text, font=font(48),
               fill=(40, 40, 40))
    return img


def flowchart(rng: random.Random, i: int) -> Image.Image:
    img = Image.new("RGB", (1400, 1000), (250, 250, 250))
    d = ImageDraw.Draw(img)
    steps = ["User request", "Firewall", "Authentication", "Authorisation",
             "Application", "Audit log"]
    for k, text in enumerate(steps):
        x = 120 + (k % 3) * 420
        y = 150 + (k // 3) * 420
        d.rounded_rectangle([x, y, x + 320, y + 140], 18,
                            fill=(225, 238, 255), outline=(40, 90, 170),
                            width=4)
        d.text((x + 30, y + 50), text, font=font(34), fill=(20, 40, 80))
        if k % 3 != 2:
            d.line([x + 320, y + 70, x + 420, y + 70], fill=(40, 90, 170),
                   width=5)
    return img


MAKERS = (
    ("security-poster", security_poster, 8),
    ("terminal", terminal, 3),
    ("login-screen", login_screen, 3),
    ("bar-chart", bar_chart, 3),
    ("slide", slide, 3),
    ("flowchart", flowchart, 3),
)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = []
    for name, maker, count in MAKERS:
        for i in range(count):
            rng = random.Random(f"{name}-{i}")
            img = maker(rng, i)
            # Posters arrive as JPEG, screenshots as PNG, the way they
            # would be shared.
            ext = ".png" if name in ("terminal", "login-screen") else ".jpg"
            path = OUT / f"{name}-{i + 1:02d}{ext}"
            if ext == ".jpg":
                img.save(path, quality=88)
            else:
                img.save(path)
            manifest.append({"file": path.name, "category": name,
                             "expected": "not_a_photo",
                             "licence": "generated by this script"})
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2),
                                       encoding="utf-8")
    print(f"{len(manifest)} images in {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
