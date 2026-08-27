# Prioclen Consulting (PCL) Visual Identity

## Colours

### Core brand
| Token | Hex | Use |
|-------|-----|-----|
| PCL Green (**primary**) | `#078045` | Titles, chrome, primary accent, logo mark. Set in `src/styles/tokens.css` as `--pcl-green`. |
| PCL Green Deep (**secondary**) | `#016837` | The deeper green: gradients, dark-theme chrome, the inner arc of the logo mark. `--pcl-green-deep` |
| PCL Green Light | `#35c98a` | Primary lightened for legibility on dark backgrounds (titles, kickers, labels in dark theme). `--pcl-green-light` |
| PCL Orange | `#f2622e` | The "full-stop" accent, emphasis, alert and "bad" states. `--pcl-orange` |
| White (**secondary**) | `#ffffff` | Surfaces in light theme; text on brand green. `--pcl-white` |

### Neutrals (light theme)
| Token | Hex |
|-------|-----|
| Header grey (text) | `#333333` |
| Body grey (muted text) | `#666666` |
| Light grey (page bg / surface-2) | `#f4f6f5` |
| Mid grey | `#e5e5e5` |
| Background / surface | `#ffffff` |

### Neutrals (dark theme)
| Token | Hex |
|-------|-----|
| Background | `#0f1613` |
| Surface | `#18211d` |
| Surface-2 | `#202b25` |
| Text | `#e8f1eb` |
| Text muted | `#9aada2` |

### Status
| Token | Base | Tint |
|-------|------|------|
| Success | `#008800` | `#dff4df` |
| Warning | `#bd5800` | `#ffe4cc` |
| Error | `#cc3333` | `#f9e6e6` |
| Info | `#0074e0` | `#deefff` |

### Severity scale (finding badges)
| Level | Hex |
|-------|-----|
| Critical | `#8c1ac4` |
| High | `#ea5448` |
| Medium | `#ef8652` |
| Low | `#f7ce55` |

## Typography

**Aptos** — the brand typeface, self-hosted woff2 (in `assets/fonts/`), subset to Latin
and base64-embedded at build. Weights: Light 300, Regular 400, SemiBold 500–600,
Bold 700–800, Black 900, plus Regular and Bold italics.

**Headers** are set in **Aptos Display**; **body copy** in **Aptos Regular**.

CSS stacks:
```
--font-sans:    "Aptos", system-ui, -apple-system, sans-serif;          /* body   */
--font-display: "Aptos Display", "Aptos", system-ui, sans-serif;        /* headers */
```

> **Aptos Display is not redistributed with Microsoft Office on every platform**, and it
> is not present on the machine this template was branded on — so headers currently
> render in Aptos (the two share the same skeleton). To use the real display face, drop
> `AptosDisplay-Regular-web.woff2` and `AptosDisplay-Bold-web.woff2` into
> `assets/fonts/`; `build.js` detects them and emits the `@font-face` rules
> automatically. No other change is needed.

## Logo

The master artwork supplied by the brand is `assets/brand/PCL LOGO SVG.svg` (a square,
stacked lockup on a 512×512 canvas). Three deck-ready SVGs are derived from it — the
wordmark is converted to outlines, so no font is needed at runtime:

| File | Shape | Used for |
|------|-------|----------|
| `pcl-logo.svg` | horizontal lockup — mark + wordmark side by side | `__LOGO_SVG__` — title and closer slides (`.title-logo`, 64px) |
| `pcl-mark.svg` | mark only | `__LOGO_MARK_SVG__` — the topbar and presenter console (26px), where a wordmark would be illegible |
| `pcl-logo-stacked.svg` | the supplied stacked arrangement, tightly cropped | available if you prefer the original lockup |

The mark's colours are emitted as CSS variables with the brand hex as the fallback, so
the logo follows the theme: `--logo-green`, `--logo-green-deep`, `--logo-word`,
`--logo-ink` (defined per theme in `src/styles/tokens.css`). In dark theme the wordmark
and the centre dot go white and the greens lighten.

To regenerate the derived SVGs after changing the master artwork, see
`assets/brand/README.md`.
