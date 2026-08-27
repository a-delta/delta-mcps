# Brand assets

| File | What it is |
|------|------------|
| `PCL LOGO SVG.svg` | **The master artwork**, exactly as supplied by the brand. Don't edit — it is the source the others are derived from. |
| `pcl-logo.svg` | Horizontal lockup (mark + wordmark). Inlined at build as `__LOGO_SVG__`. |
| `pcl-mark.svg` | Mark only, for the 26px topbar / console slots. Inlined as `__LOGO_MARK_SVG__`. |
| `pcl-logo-stacked.svg` | The supplied stacked arrangement, tightly cropped. Not wired up; swap it in if you prefer it. |
| `make-logos.py` | Regenerates the three derived SVGs from the master. |

The master is a 512×512 stacked lockup whose wordmark is only ~5% of the canvas height —
at the 26–64px the deck renders a logo at, it would be an illegible smudge. The derived
files fix that: `pcl-logo.svg` sets the wordmark beside the mark at ~28% of its height,
and `pcl-mark.svg` drops the wordmark entirely for the small slots.

The wordmark is **converted to outlines** from Aptos Bold, so the logo needs no font at
runtime. Colours are emitted as CSS variables with the brand hex as a fallback
(`--logo-green`, `--logo-green-deep`, `--logo-word`, `--logo-ink`), defined per theme in
`src/styles/tokens.css` — so the logo follows light/dark.

## Regenerating

```bash
pip install fonttools brotli
python3 assets/brand/make-logos.py
node build.js
```

The script reads Aptos Bold from the Microsoft Office bundle
(`/Applications/Microsoft PowerPoint.app/Contents/Resources/DFonts/Aptos-Bold.ttf`);
point `FONT` at another copy if yours lives elsewhere.
