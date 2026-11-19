"""Generate app/desktop/palettes.py from the web stylesheet palettes.

The web GUI defines its 24 color palettes (x dark/light theme) as CSS custom
properties, partly through var() chains and color-mix().  The desktop window
uses the same colors, so this script evaluates those blocks the way the
browser cascade does (same specificity -> source order) and writes plain RGBA
values.

Usage:  python scripts/generate_desktop_palettes.py
"""

from __future__ import annotations

import pprint
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS_PATH = ROOT / "app" / "static" / "css" / "style.css"
OUTPUT_PATH = ROOT / "app" / "desktop" / "palettes.py"

KEYS = (
    "bg", "bg-layer", "sidebar", "panel", "panel-alt", "panel-soft", "panel-emphasis",
    "border", "text", "muted", "surface", "accent", "accent-strong", "accent-soft",
    "focus-ring", "hover-layer", "border-hover", "danger", "success", "warning", "code",
    "secondary", "secondary-strong", "accent-2", "nav-selection-bg", "nav-selection-border",
    "notice-bg", "notice-color", "notice-border", "page-glow",
    "traffic-color-own-beacon-tx", "traffic-color-own-beacon-rx", "traffic-color-own-wx-tx",
    "traffic-color-own-wx-rx", "traffic-color-own-message-tx", "traffic-color-own-message-rx",
    "traffic-color-repeated-tx", "traffic-color-proxy-tx", "traffic-color-aprsis-rx",
    "traffic-color-aprsis-to-rf-tx",
    "alert-detail-map-area-yellow", "alert-detail-map-area-orange", "alert-detail-map-area-red",
    "alert-detail-map-area-gray", "alert-detail-map-area-halo", "alert-detail-map-area-fill-opacity",
    "map-mask-layer-color", "map-mask-default-opacity",
    "logo-bg-inner", "logo-border", "logo-text", "logo-accent", "logo-symbol",
    "logo-gradient-from", "logo-gradient-to",
)

NAMED = {
    "transparent": (0, 0, 0, 0.0),
    "white": (255, 255, 255, 1.0),
    "black": (0, 0, 0, 1.0),
}


def parse_blocks(css: str) -> list[tuple[str, dict[str, str]]]:
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    blocks: list[tuple[str, dict[str, str]]] = []
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selector = " ".join(match.group(1).split())
        if not selector.startswith(":root"):
            continue
        declarations: dict[str, str] = {}
        for raw in match.group(2).split(";"):
            if ":" not in raw:
                continue
            name, value = raw.split(":", 1)
            name = name.strip()
            if name.startswith("--"):
                declarations[name[2:]] = " ".join(value.split())
        if declarations:
            blocks.append((selector, declarations))
    return blocks


def selector_matches(selector: str, theme: str, palette: str) -> bool:
    # Only selectors that style :root itself define tokens; descendant rules
    # such as ':root[data-palette="x"] .sidebar' are skipped.
    compact = selector[len(":root"):].replace(" ", "")
    if not re.fullmatch(r"(\[[^\]]+\])*(:is\([^)]*\))?", compact):
        return False
    for attr, value in re.findall(r'\[data-([a-z-]+)="([^"]+)"\](?![^(]*\))', compact):
        if attr == "theme" and value != theme:
            return False
        if attr == "palette" and value != palette:
            return False
        if attr not in {"theme", "palette"}:
            return False
    is_match = re.search(r":is\(([^)]*)\)", compact)
    if is_match:
        palettes = re.findall(r'\[data-palette="([^"]+)"\]', is_match.group(1))
        if palettes and palette not in palettes:
            return False
    return True


def parse_color(text: str) -> tuple[float, float, float, float]:
    text = text.strip().lower()
    if text in NAMED:
        return NAMED[text]
    if text.startswith("#"):
        hex_text = text[1:]
        if len(hex_text) in (3, 4):
            hex_text = "".join(ch * 2 for ch in hex_text)
        r, g, b = int(hex_text[0:2], 16), int(hex_text[2:4], 16), int(hex_text[4:6], 16)
        a = int(hex_text[6:8], 16) / 255 if len(hex_text) == 8 else 1.0
        return (r, g, b, a)
    match = re.fullmatch(r"rgba?\(([^)]*)\)", text)
    if match:
        parts = [part.strip() for part in re.split(r"[,\s/]+", match.group(1)) if part.strip()]
        r, g, b = (float(parts[0]), float(parts[1]), float(parts[2]))
        a = float(parts[3]) if len(parts) > 3 else 1.0
        return (r, g, b, a)
    raise ValueError(f"Unsupported color: {text}")


def split_args(text: str) -> list[str]:
    depth = 0
    current = ""
    parts: list[str] = []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append(current.strip())
            current = ""
            continue
        current += ch
    if current.strip():
        parts.append(current.strip())
    return parts


def evaluate(value: str, variables: dict[str, str], depth: int = 0) -> str:
    if depth > 30:
        raise ValueError("var() recursion too deep")
    value = value.strip()

    def replace_var(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in variables:
            raise KeyError(name)
        return evaluate(variables[name], variables, depth + 1)

    while "var(--" in value:
        value = re.sub(r"var\(--([a-z0-9-]+)\)", replace_var, value, count=1)
    if value.startswith("color-mix("):
        inner = value[len("color-mix("):-1]
        args = split_args(inner)
        if not args or not args[0].startswith("in srgb"):
            raise ValueError(value)
        first, second = args[1], args[2]

        def color_and_pct(part: str) -> tuple[tuple[float, float, float, float], float | None]:
            match = re.fullmatch(r"(.*?)\s+([0-9.]+)%", part.strip())
            if match:
                return parse_color(evaluate(match.group(1), variables, depth + 1)), float(match.group(2)) / 100
            return parse_color(evaluate(part, variables, depth + 1)), None

        c1, p1 = color_and_pct(first)
        c2, p2 = color_and_pct(second)
        if p1 is None and p2 is None:
            p1 = p2 = 0.5
        elif p1 is None:
            p1 = 1 - p2
        elif p2 is None:
            p2 = 1 - p1
        total = p1 + p2
        p1, p2 = p1 / total, p2 / total
        alpha = c1[3] * p1 + c2[3] * p2
        if alpha <= 0:
            return "rgba(0, 0, 0, 0)"
        rgb = [
            (c1[i] * c1[3] * p1 + c2[i] * c2[3] * p2) / alpha
            for i in range(3)
        ]
        return format_color((rgb[0], rgb[1], rgb[2], alpha))
    try:
        return format_color(parse_color(value))
    except ValueError:
        return value


def format_color(color: tuple[float, float, float, float]) -> str:
    r, g, b, a = (int(round(color[0])), int(round(color[1])), int(round(color[2])), color[3])
    if a >= 0.999:
        return f"#{r:02x}{g:02x}{b:02x}"
    return f"rgba({r}, {g}, {b}, {round(a, 3)})"


def main() -> None:
    import sys

    sys.path.insert(0, str(ROOT))
    from app.ui_palette import UI_PALETTE_OPTIONS

    blocks = parse_blocks(CSS_PATH.read_text(encoding="utf-8"))
    result: dict[str, dict[str, dict[str, str]]] = {}
    for option in UI_PALETTE_OPTIONS:
        palette = option["value"]
        result[palette] = {}
        for theme in ("dark", "light"):
            variables: dict[str, str] = {}
            for selector, declarations in blocks:
                if selector_matches(selector, theme, palette):
                    variables.update(declarations)
            resolved: dict[str, str] = {}
            for key in KEYS:
                if key not in variables:
                    continue
                try:
                    resolved[key] = evaluate(variables[key], variables)
                except (KeyError, ValueError):
                    continue
            resolved.setdefault("secondary", resolved.get("accent", "#6dbd80"))
            resolved.setdefault("secondary-strong", resolved.get("accent-strong", "#4e9f63"))
            resolved.setdefault("accent-2", resolved.get("accent", "#6dbd80"))
            result[palette][theme] = resolved

    header = (
        '"""Color palettes of the desktop window.\n\n'
        "Generated by scripts/generate_desktop_palettes.py from app/static/css/style.css;\n"
        'do not edit by hand.\n"""\n\n'
    )
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        header + "PALETTES: dict[str, dict[str, dict[str, str]]] = " + pprint.pformat(result, width=110, sort_dicts=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(f"Wrote {OUTPUT_PATH} ({len(result)} palettes)")


if __name__ == "__main__":
    main()
