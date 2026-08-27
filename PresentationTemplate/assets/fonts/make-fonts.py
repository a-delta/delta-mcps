#!/usr/bin/env python3
"""Subset the Aptos TTFs shipped with Microsoft Office into Latin-only woff2.

    pip install fonttools brotli
    python3 assets/fonts/make-fonts.py

Full Aptos covers Cyrillic/Greek and a large symbol set; the deck only needs Latin,
so subsetting takes each face from ~250KB to ~23KB — the whole family embeds in the
built HTML comfortably — seven faces for under 165KB before base64.

Aptos Display (the brand's header face) is NOT bundled with Office on macOS. If you
obtain it, subset it the same way to AptosDisplay-Regular-web.woff2 /
AptosDisplay-Bold-web.woff2 — build.js picks them up automatically.
"""
import os, subprocess, sys

SRC = "/Applications/Microsoft PowerPoint.app/Contents/Resources/DFonts"
OUT = os.path.dirname(os.path.abspath(__file__))

# Basic + extended Latin, punctuation, currency, and the arrows/marks the deck's
# chrome and slide copy use.
UNICODES = ",".join([
    "U+0000-00FF", "U+0100-017F", "U+0180-024F",   # Latin + extended
    "U+2000-206F", "U+20A0-20BF", "U+2122",        # punctuation, currency, ™
    "U+2190-21BB", "U+2202", "U+2212", "U+2215",   # arrows + maths
    "U+2248", "U+2260", "U+2264", "U+2265",
    "U+25A0-25FF", "U+2713", "U+2717",             # geometric shapes, ✓ ✗
])

FACES = {
    "Aptos-Light.ttf":       "Aptos-Light-web.woff2",
    "Aptos.ttf":             "Aptos-Regular-web.woff2",
    "Aptos-SemiBold.ttf":    "Aptos-SemiBold-web.woff2",
    "Aptos-Bold.ttf":        "Aptos-Bold-web.woff2",
    "Aptos-Black.ttf":       "Aptos-Black-web.woff2",
    "Aptos-Italic.ttf":      "Aptos-Italic-web.woff2",
    "Aptos-Bold-Italic.ttf": "Aptos-BoldItalic-web.woff2",
}

for src, dst in FACES.items():
    path = os.path.join(SRC, src)
    if not os.path.exists(path):
        print(f"  ! missing {src} — is Microsoft Office installed?")
        continue
    out = os.path.join(OUT, dst)
    subprocess.run([sys.executable, "-m", "fontTools.subset", path,
                    f"--unicodes={UNICODES}", "--layout-features=*", "--flavor=woff2",
                    "--desubroutinize", "--no-hinting", "--drop-tables+=DSIG",
                    f"--output-file={out}"], check=True)
    print(f"{dst:30s} {os.path.getsize(out)/1024:6.1f} KB")
