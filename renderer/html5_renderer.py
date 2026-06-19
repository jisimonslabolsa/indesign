"""
JSON Layout → HTML5 Banner Renderer

Generates a production-ready HTML5 banner zip from a layout JSON.
Output is compatible with DV360, Xandr, Adform, and other IAB-compliant ad servers.

IAB sizes supported (auto-detected from canvas):
  300x250  Medium Rectangle
  728x90   Leaderboard
  160x600  Wide Skyscraper
  300x600  Half Page
  320x50   Mobile Banner
  970x250  Billboard
  300x50   Mobile Banner (small)
"""

import json
import os
import zipfile
import shutil
from pathlib import Path

# IAB standard sizes for reference
IAB_SIZES = {
    (300, 250): "Medium Rectangle",
    (728, 90):  "Leaderboard",
    (160, 600): "Wide Skyscraper",
    (300, 600): "Half Page",
    (320, 50):  "Mobile Banner",
    (970, 250): "Billboard",
    (300, 50):  "Mobile Banner Small",
    (320, 100): "Large Mobile Banner",
}


def build_font_css(fonts: list) -> str:
    """
    Build @font-face CSS blocks for uploaded fonts.
    fonts: [{"family": "Sybarite", "filename": "abc123_Sybarite.woff2"}]
    Falls back to Arial if no fonts provided.
    """
    if not fonts:
        return ""
    lines = []
    for f in fonts:
        family  = f.get("family", "")
        fname   = f.get("filename", "")
        if not family or not fname:
            continue
        ext = os.path.splitext(fname)[1].lower().lstrip(".")
        fmt_map = {"woff2": "woff2", "woff": "woff", "ttf": "truetype", "otf": "opentype"}
        fmt = fmt_map.get(ext, "truetype")
        lines.append(
            f'    @font-face {{\n'
            f'      font-family: "{family}";\n'
            f'      src: url("fonts/{fname}") format("{fmt}");\n'
            f'      font-weight: normal;\n'
            f'      font-style: normal;\n'
            f'    }}'
        )
    return "\n".join(lines)


def render_element_css(el: dict) -> str:
    """Convert a layout element to CSS absolute positioning block."""
    styles = [
        f"position: absolute",
        f"left: {el['x']}px",
        f"top: {el['y']}px",
        f"width: {el['width']}px",
        f"height: {el['height']}px",
        f"z-index: {el['zIndex']}",
    ]

    if el.get("opacity", 1) < 1:
        styles.append(f"opacity: {el['opacity']}")

    if "backgroundColor" in el:
        styles.append(f"background-color: {el['backgroundColor']}")

    if "borderColor" in el and "borderWidth" in el:
        styles.append(f"border: {el['borderWidth']}px solid {el['borderColor']}")

    if "borderRadius" in el:
        styles.append(f"border-radius: {el['borderRadius']}px")

    return "; ".join(styles)


def render_text_element(el: dict, idx: int, available_fonts: set = None) -> str:
    """Render a text frame element with all paragraphs and runs."""
    css = render_element_css(el)
    inner_html = []
    available_fonts = available_fonts or set()

    for para in el.get("paragraphs", []):
        para_parts = []
        for run in para.get("runs", []):
            font_family = run.get("fontFamily", "")
            # Only use custom font if it was uploaded, otherwise Arial
            if font_family and font_family in available_fonts:
                font_stack = f'"{font_family}", Arial, sans-serif'
            else:
                font_stack = "Arial, sans-serif"
            span_styles = [f"font-size: {run['size']}px", f"font-family: {font_stack}"]
            if run.get("color"):
                span_styles.append(f"color: {run['color']}")
            if run.get("bold"):
                span_styles.append("font-weight: bold")
            if run.get("italic"):
                span_styles.append("font-style: italic")
            style_str = "; ".join(span_styles)
            text = run["text"].replace("&", "&amp;").replace("<", "&lt;")
            para_parts.append(f'<span style="{style_str}">{text}</span>')

        inner_html.append(
            f'<p style="margin:0;padding:0;line-height:1.2">{"".join(para_parts)}</p>'
        )

    return f'<div id="el{idx}" style="{css}; overflow:hidden">{"".join(inner_html)}</div>'


def render_image_element(el: dict, idx: int) -> str:
    """Render an image element (placeholder if src not found locally)."""
    css = render_element_css(el)
    src = el.get("src", "")
    filename = os.path.basename(src) if src else ""

    # Use actual file if it's been copied to assets/, else use placeholder
    img_src = f"assets/{filename}" if filename else ""
    placeholder_style = (
        f"width:{el['width']}px;height:{el['height']}px;"
        f"background:{el.get('backgroundColor','#cccccc')};"
        f"display:flex;align-items:center;justify-content:center;"
        f"color:#fff;font-size:11px;font-family:sans-serif;"
    )

    if img_src:
        return (
            f'<div id="el{idx}" style="{css}">'
            f'<img src="{img_src}" style="width:100%;height:100%;object-fit:cover" '
            f'onerror="this.parentNode.innerHTML=\'<div style=&quot;{placeholder_style}&quot;>IMG</div>\'">'
            f'</div>'
        )
    else:
        return (
            f'<div id="el{idx}" style="{css};{placeholder_style}">'
            f'<span>IMAGE</span>'
            f'</div>'
        )



def render_svg_group(el: dict, idx: int) -> str:
    """Render a vector group as inline SVG."""
    x = el['x']
    y = el['y']
    w = el['width']
    h = el['height']
    z = el.get('zIndex', 0)
    opacity = el.get('opacity', 1)
    vb = el.get('viewBox', f'0 0 {w} {h}')
    paths = '\n    '.join(el.get('paths', []))
    op_style = f'; opacity:{opacity}' if opacity < 1 else ''
    return (
        f'<div id="el{idx}" style="position:absolute;left:{x}px;top:{y}px;'
        f'width:{w}px;height:{h}px;z-index:{z}{op_style}">\n'
        f'  <svg xmlns="http://www.w3.org/2000/svg" viewBox="{vb}" '
        f'width="100%" height="100%" preserveAspectRatio="xMidYMid meet">\n'
        f'    {paths}\n'
        f'  </svg>\n'
        f'</div>'
    )

def render_shape_element(el: dict, idx: int) -> str:
    """Render a rectangle or other shape element."""
    css = render_element_css(el)
    return f'<div id="el{idx}" style="{css}"></div>'


def build_html(layout: dict, click_url: str = "%%CLICK_URL_UNESC%%", fonts: list = None) -> str:
    """Build the complete HTML5 banner document."""
    canvas = layout["canvas"]
    w = int(canvas["width"])
    h = int(canvas["height"])
    size_name = IAB_SIZES.get((w, h), f"{w}x{h}")

    elements_html = []
    available_fonts = {f.get("family", "") for f in (fonts or [])}
    for idx, el in enumerate(layout.get("elements", [])):
        etype = el.get("type", "rectangle")
        if etype == "text":
            elements_html.append(render_text_element(el, idx, available_fonts))
        elif etype == "image":
            elements_html.append(render_image_element(el, idx))
        elif etype == "svg_group":
            elements_html.append(render_svg_group(el, idx))
        else:
            elements_html.append(render_shape_element(el, idx))

    font_css = build_font_css(fonts or [])
    elements_str = "\n    ".join(elements_html)

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="ad.size" content="width={w},height={h}">
  <title>{size_name} Banner</title>
  <style>
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    {font_css}
    html, body {{
      width: {w}px;
      height: {h}px;
      overflow: hidden;
      font-family: Arial, Helvetica, sans-serif;
    }}
    #banner {{
      position: relative;
      width: {w}px;
      height: {h}px;
      overflow: hidden;
      cursor: pointer;
    }}
    #border {{
      position: absolute;
      top: 0; left: 0;
      width: {w - 1}px;
      height: {h - 1}px;
      border: 1px solid rgba(0,0,0,0.15);
      z-index: 9999;
      pointer-events: none;
    }}
  </style>
</head>
<body>
  <div id="banner" onclick="clickThrough()">
    {elements_str}
    <div id="border"></div>
  </div>

  <script>
    // ── IAB ClickTag ──────────────────────────────────────────
    var clickTag = "{click_url}";

    function clickThrough() {{
      if (clickTag && clickTag !== "" && !clickTag.startsWith("%%")) {{
        window.open(clickTag, "_blank");
      }}
    }}

    // ── Optional: simple CSS animation on load ────────────────
    document.addEventListener("DOMContentLoaded", function() {{
      var banner = document.getElementById("banner");
      banner.style.opacity = "0";
      banner.style.transition = "opacity 0.3s ease";
      setTimeout(function() {{
        banner.style.opacity = "1";
      }}, 50);
    }});
  </script>
</body>
</html>"""


def build_manifest(layout: dict) -> str:
    """Generate a basic manifest.json for ad servers that require it."""
    canvas = layout["canvas"]
    return json.dumps({
        "name": layout.get("source", "banner"),
        "version": "1.0",
        "width": int(canvas["width"]),
        "height": int(canvas["height"]),
        "main": "index.html",
    }, indent=2)


def render(
    layout_path: str,
    output_dir: str = "output",
    assets_dir: str = None,
    click_url: str = "%%CLICK_URL_UNESC%%",
    fonts: list = None,
) -> str:
    """
    Main entry point.
    Returns path to generated .zip file.
    """
    with open(layout_path, encoding="utf-8") as f:
        layout = json.load(f)

    canvas = layout["canvas"]
    w, h = int(canvas["width"]), int(canvas["height"])
    banner_name = f"banner_{w}x{h}"
    banner_dir = os.path.join(output_dir, banner_name)

    # Clean and create output dir
    if os.path.exists(banner_dir):
        shutil.rmtree(banner_dir)
    os.makedirs(os.path.join(banner_dir, "assets"), exist_ok=True)

    # Write index.html
    html = build_html(layout, click_url, fonts=fonts or [])
    html_path = os.path.join(banner_dir, "index.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)

    # Write manifest.json
    with open(os.path.join(banner_dir, "manifest.json"), "w") as f:
        f.write(build_manifest(layout))

    # Copy image assets if provided
    if assets_dir and os.path.isdir(assets_dir):
        for el in layout.get("elements", []):
            if el.get("type") == "image" and el.get("src"):
                filename = os.path.basename(el["src"])
                src_path = os.path.join(assets_dir, filename)
                if os.path.exists(src_path):
                    shutil.copy(src_path, os.path.join(banner_dir, "assets", filename))


    # Copy font files into banner
    fonts_dir_src = os.environ.get("FONTS_DIR", "/app/fonts")
    if fonts:
        os.makedirs(os.path.join(banner_dir, "fonts"), exist_ok=True)
        for font in fonts:
            fname = font.get("filename", "")
            if fname:
                src_path = os.path.join(fonts_dir_src, fname)
                if os.path.exists(src_path):
                    shutil.copy(src_path, os.path.join(banner_dir, "fonts", fname))

    # Generate fallback.jpg (white placeholder — replace with real screenshot)
    _generate_fallback(banner_dir, w, h, layout)

    # Package as zip
    zip_path = os.path.join(output_dir, f"{banner_name}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, _, files in os.walk(banner_dir):
            for fname in files:
                fpath = os.path.join(root, fname)
                arcname = os.path.relpath(fpath, banner_dir)
                zf.write(fpath, arcname)

    size_name = IAB_SIZES.get((w, h), f"{w}x{h}")
    print(f"Banner generated → {zip_path}  [{size_name}]")
    return zip_path


def _generate_fallback(banner_dir: str, w: int, h: int, layout: dict):
    """Generate a simple fallback PNG using Pillow."""
    try:
        from PIL import Image, ImageDraw, ImageFont

        # Get background color from first rectangle element
        bg_color = "#1a1a4a"
        for el in layout.get("elements", []):
            if el.get("type") == "rectangle" and "backgroundColor" in el:
                bg_color = el["backgroundColor"]
                break

        # Convert hex to RGB
        bg_color = bg_color.lstrip("#")
        rgb = tuple(int(bg_color[i:i+2], 16) for i in (0, 2, 4))

        img = Image.new("RGB", (w, h), rgb)
        draw = ImageDraw.Draw(img)

        # Draw text elements as simple text
        for el in layout.get("elements", []):
            if el.get("type") == "text":
                for para in el.get("paragraphs", []):
                    for run in para.get("runs", []):
                        text = run.get("text", "")
                        color_hex = run.get("color", "#ffffff").lstrip("#")
                        color = tuple(int(color_hex[i:i+2], 16) for i in (0, 2, 4))
                        x = max(0, int(el["x"]))
                        y = max(0, int(el["y"]))
                        try:
                            draw.text((x, y), text, fill=color)
                        except Exception:
                            pass

        img.save(os.path.join(banner_dir, "fallback.jpg"), "JPEG", quality=85)
    except Exception as e:
        print(f"  [warn] fallback: {e}")
        # Write empty placeholder
        Path(os.path.join(banner_dir, "fallback.jpg")).touch()


if __name__ == "__main__":
    import sys
    layout_path = sys.argv[1] if len(sys.argv) > 1 else "output/layout.json"
    output_dir  = sys.argv[2] if len(sys.argv) > 2 else "output"
    render(layout_path, output_dir)
