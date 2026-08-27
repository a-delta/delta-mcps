"""Derive the deck's logo SVGs from the supplied PCL master artwork.

    python3 assets/brand/make-logos.py        (needs: pip install fonttools brotli)

Emits three files into assets/brand/ :
  pcl-logo.svg          horizontal lockup (mark + wordmark) — the deck's default
  pcl-mark.svg          mark only — for small chrome slots
  pcl-logo-stacked.svg  the supplied stacked arrangement, tightly cropped

The wordmark is converted to outlines from Aptos Bold, so the logo needs no
font at runtime. Colours are emitted as CSS vars (with the brand hex as the
fallback) so the logo follows the deck's light/dark theme.
"""
import re, os
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.transformPen import TransformPen
from fontTools.misc.transform import Transform
from fontTools.svgLib.path import parse_path

BRAND = os.path.dirname(os.path.abspath(__file__))
raw = open(os.path.join(BRAND, "PCL LOGO SVG.svg")).read()

paths  = re.findall(r'<path fill="(#[0-9A-Fa-f]{6})" d="([^"]+)"', raw)
circle = re.search(r'<circle fill="(#[0-9A-Fa-f]{6})" cx="([\d.]+)" cy="([\d.]+)" r="([\d.]+)"', raw)
m = re.search(r'<text transform="matrix\(1 0 0 1 ([\d.]+) ([\d.]+)\)" fill="(#[0-9A-Fa-f]{6})"'
              r' font-family="[^"]*" font-size="([\d.]+)">([^<]+)</text>', raw)
tx, ty, tsize, ttext = float(m[1]), float(m[2]), float(m[4]), m[5]

FONT = "/Applications/Microsoft PowerPoint.app/Contents/Resources/DFonts/Aptos-Bold.ttf"
font = TTFont(FONT); upem = font["head"].unitsPerEm
gs, cmap, hmtx = font.getGlyphSet(), font.getBestCmap(), font["hmtx"]
num = lambda v: f"{v:.2f}".rstrip("0").rstrip(".")

def wordmark(size, x0, baseline):
    """Outline `ttext` at `size`, starting at x0 with the given baseline."""
    pen, bp = SVGPathPen(gs, ntos=num), BoundsPen(gs)
    s, x = size / upem, x0
    for ch in ttext:
        g = cmap.get(ord(ch))
        if not g:
            x += size * 0.5; continue
        t = Transform(s, 0, 0, -s, x, baseline)
        gs[g].draw(TransformPen(pen, t)); gs[g].draw(TransformPen(bp, t))
        x += hmtx[g][0] * s
    return pen.getCommands(), bp.bounds, x - x0

def mark_bbox():
    bp = BoundsPen(None)
    for _, d in paths: parse_path(d, bp)
    b = list(bp.bounds)
    cx, cy, r = float(circle[2]), float(circle[3]), float(circle[4])
    return [min(b[0], cx-r), min(b[1], cy-r), max(b[2], cx+r), max(b[3], cy+r)]

def mark_body(dx=0.0, dy=0.0, indent="  "):
    """The mark's paths, optionally translated by (dx, dy)."""
    wrap = lambda inner: (inner if dx == dy == 0 else
                          f'<g transform="translate({num(dx)} {num(dy)})">\n{inner}\n{indent}</g>')
    out = []
    for fill, d in paths:
        var = "--logo-green-deep,#016837" if fill.lower() == "#006837" else "--logo-green,#078045"
        out.append(f'{indent}  <path fill="var({var})" d="{re.sub(r"\s*\n\s*", " ", d).strip()}"/>')
    out.append(f'{indent}  <circle fill="var(--logo-ink,#141414)" '
               f'cx="{circle[2]}" cy="{circle[3]}" r="{circle[4]}"/>')
    return wrap("\n".join(out)) if (dx or dy) else "\n".join(o[2:] for o in out)

def svg(bb, pad):
    x0, y0, x1, y1 = bb[0]-pad, bb[1]-pad, bb[2]+pad, bb[3]+pad
    f = lambda v: f"{v:.1f}".rstrip("0").rstrip(".")
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{f(x0)} {f(y0)} {f(x1-x0)} {f(y1-y0)}"'
            f' role="img" aria-label="Prioclen Consulting">\n'
            f'  <title>Prioclen Consulting</title>\n')

MB = mark_bbox()
mw, mh = MB[2]-MB[0], MB[3]-MB[1]

# ---- 1. horizontal lockup ------------------------------------------------
# Wordmark cap height set to 28% of the mark height so it still reads at the
# 26–72px the deck renders the logo at; optically centred on the mark.
CAP_RATIO = 14.04 / 21.04                       # cap height per em, measured off the master
size = (0.28 * mh) / CAP_RATIO
gap  = 0.24 * mw
cap  = 0.28 * mh
baseline = MB[1] + mh/2 + cap/2
d, wbb, wwidth = wordmark(size, MB[2] + gap, baseline)
full = (svg([MB[0], MB[1], MB[2] + gap + wwidth, MB[3]], 6)
        + mark_body() + "\n"
        + f'  <path fill="var(--logo-word,#078045)" d="{d}"/>\n</svg>\n')
open(os.path.join(BRAND, "pcl-logo.svg"), "w").write(full)

# ---- 2. mark only --------------------------------------------------------
open(os.path.join(BRAND, "pcl-mark.svg"), "w").write(svg(MB, 4) + mark_body() + "\n</svg>\n")

# ---- 3. stacked (as supplied), tightly cropped ---------------------------
d2, wbb2, _ = wordmark(tsize, tx, ty)
sb = [min(MB[0], wbb2[0]), min(MB[1], wbb2[1]), max(MB[2], wbb2[2]), max(MB[3], wbb2[3])]
open(os.path.join(BRAND, "pcl-logo-stacked.svg"), "w").write(
    svg(sb, 6) + mark_body() + "\n" + f'  <path fill="var(--logo-word,#078045)" d="{d2}"/>\n</svg>\n')

for f in ("pcl-logo.svg", "pcl-mark.svg", "pcl-logo-stacked.svg"):
    p = os.path.join(BRAND, f)
    vb = re.search(r'viewBox="([^"]+)"', open(p).read())[1].split()
    print(f"{f:22s} {os.path.getsize(p)//1024+1:3d} KB  viewBox {vb}  ratio "
          f"{float(vb[2])/float(vb[3]):.2f}")
