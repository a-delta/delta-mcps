# Tesco Visual Identity

## Colours

### Core brand
| Token | Hex | Use |
|-------|-----|-----|
| Tesco Blue (**deck**) | `#003ADC` | **The deck's primary blue** — titles, chrome, flywheel. Set in `src/styles/tokens.css` as `--tesco-blue`. A brighter, more saturated blue chosen for on-screen/projector legibility. |
| Tesco Blue (historical) | `#00539f` | The older brand blue from the Reporting tool. Kept here for reference; the deck deliberately diverges to `#003ADC`. |
| Tesco Red ("full stop" red) | `#e81c2d` | Accent / emphasis (the dot in the logo) |
| Logo Red (SVG) | `#EE1C2E` | The "TESCO" letters in the wordmark SVG |
| Logo Blue (SVG) | `#00539F` | The stripes under the wordmark |

### Neutrals (light theme)
| Token | Hex |
|-------|-----|
| Header grey (text) | `#333333` |
| Body grey (muted text) | `#666666` |
| Light grey (surface-2) | `#f6f6f6` |
| Mid grey | `#e5e5e5` |
| Background / surface | `#ffffff` |

### Neutrals (dark theme)
| Token | Hex |
|-------|-----|
| Background | `#11151c` |
| Surface | `#1a2029` |
| Surface-2 | `#222a35` |
| Text | `#e8ecf1` |
| Text muted | `#9aa6b4` |

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

**Tesco Modern** — the corporate brand typeface. Self-hosted woff2 (in `assets/fonts/`).
Weights: Light 300, Regular 400, Medium 500, Bold 700, each with matching italic.

CSS stack:
```
--font-sans: "Tesco Modern", system-ui, -apple-system, sans-serif;
```

## Logo

`assets/brand/tesco-logo.svg` — official wordmark, native 158×45. Red letters (`#EE1C2E`)
over blue stripes (`#00539F`). Inline-able SVG.
