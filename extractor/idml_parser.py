"""
IDML → JSON Extractor
Parses an IDML file and outputs a normalized layout JSON
ready for HTML5 banner rendering.

Coordinate system:
  InDesign: origin at center of page, Y grows down
  CSS output: origin at top-left, Y grows down
  Units: points → pixels (1pt = 1px at 72ppi)
"""

import zipfile
import json
import re
import os
from lxml import etree
from pathlib import Path


# ── Namespaces ────────────────────────────────────────────
NS = {
    "idPkg": "http://ns.adobe.com/AdobeInDesign/idml/1.0/packaging",
}

def pt_to_px(pt):
    """Points to pixels at 72ppi (1:1)."""
    return round(float(pt), 2)


def parse_color_value(color_value_str):
    """'255 128 0' → '#ff8000'"""
    try:
        parts = [int(float(x)) for x in color_value_str.strip().split()]
        if len(parts) == 3:
            return "#{:02x}{:02x}{:02x}".format(*parts)
        if len(parts) == 4:  # CMYK — rough conversion
            c, m, y, k = [x / 100 for x in parts]
            r = int(255 * (1 - c) * (1 - k))
            g = int(255 * (1 - m) * (1 - k))
            b = int(255 * (1 - y) * (1 - k))
            return "#{:02x}{:02x}{:02x}".format(r, g, b)
    except Exception:
        pass
    return "#000000"


def parse_transform(transform_str):
    """
    ItemTransform="a b c d tx ty"
    Returns (tx, ty) translation in points.
    """
    try:
        parts = [float(x) for x in transform_str.strip().split()]
        if len(parts) == 6:
            return parts[4], parts[5]  # tx, ty
    except Exception:
        pass
    return 0.0, 0.0


def parse_bounds(bounds_str):
    """
    GeometricBounds="y1 x1 y2 x2"  (InDesign order: top left bottom right)
    Returns (x1, y1, width, height) in points.
    """
    try:
        y1, x1, y2, x2 = [float(v) for v in bounds_str.strip().split()]
        return x1, y1, x2 - x1, y2 - y1
    except Exception:
        return 0.0, 0.0, 0.0, 0.0


class IDMLParser:
    def __init__(self, idml_path: str):
        self.idml_path = idml_path
        self.zf = zipfile.ZipFile(idml_path, "r")
        self.colors = {}      # Self → hex
        self.stories = {}     # Self → {paragraphs: [...]}
        self.page_w = 0
        self.page_h = 0

    def parse(self) -> dict:
        self._parse_styles()
        self._parse_stories()
        elements = self._parse_spreads()
        return {
            "source": os.path.basename(self.idml_path),
            "canvas": {"width": self.page_w, "height": self.page_h},
            "elements": elements,
        }

    # ── Styles / Colors ──────────────────────────────────
    def _parse_styles(self):
        try:
            xml = self.zf.read("Resources/Styles.xml")
            root = etree.fromstring(xml)
            for color in root.iter("Color"):
                self_id = color.get("Self", "")
                cv = color.get("ColorValue", "")
                space = color.get("Space", "RGB")
                if cv:
                    self.colors[self_id] = parse_color_value(cv)
                    # Also index by name for easy lookup
                    name = color.get("Name", "")
                    if name:
                        self.colors[f"Color/{name}"] = parse_color_value(cv)
        except Exception as e:
            print(f"  [warn] styles: {e}")

    # ── Stories (text content) ────────────────────────────
    def _parse_stories(self):
        for name in self.zf.namelist():
            if name.startswith("Stories/") and name.endswith(".xml"):
                try:
                    xml = self.zf.read(name)
                    root = etree.fromstring(xml)
                    for story in root.iter("Story"):
                        self_id = story.get("Self", "")
                        paragraphs = []
                        for psr in story.iter("ParagraphStyleRange"):
                            para = {
                                "style": psr.get("AppliedParagraphStyle", ""),
                                "runs": [],
                            }
                            for csr in psr.iter("CharacterStyleRange"):
                                size = csr.get("PointSize")
                                fill = csr.get("FillColor", "")
                                font_style = csr.get("FontStyle", "Regular")
                                content_nodes = csr.findall("Content")
                                text = "".join(
                                    (n.text or "") for n in content_nodes
                                )
                                if text.strip():
                                    para["runs"].append({
                                        "text": text,
                                        "size": pt_to_px(size) if size else 12,
                                        "color": self.colors.get(fill, "#000000"),
                                        "bold": "Bold" in font_style,
                                        "italic": "Italic" in font_style,
                                    })
                            if para["runs"]:
                                paragraphs.append(para)
                        if paragraphs:
                            self.stories[self_id] = paragraphs
                except Exception as e:
                    print(f"  [warn] story {name}: {e}")

    # ── Spreads (layout elements) ─────────────────────────
    def _parse_spreads(self) -> list:
        elements = []
        for name in self.zf.namelist():
            if name.startswith("Spreads/") and name.endswith(".xml"):
                try:
                    xml = self.zf.read(name)
                    root = etree.fromstring(xml)
                    for spread in root.iter("Spread"):
                        for page in spread.iter("Page"):
                            bounds_str = page.get("GeometricBounds", "")
                            if bounds_str:
                                _, _, self.page_w, self.page_h = parse_bounds(bounds_str)
                                self.page_w = pt_to_px(self.page_w)
                                self.page_h = pt_to_px(self.page_h)

                        for child in spread:
                            # Skip XML comments and processing instructions
                            if callable(child.tag):
                                continue
                            el = self._parse_element(child)
                            if el:
                                elements.append(el)
                except Exception as e:
                    print(f"  [warn] spread {name}: {e}")

        # Sort by z-index (order in XML = paint order)
        for i, el in enumerate(elements):
            el["zIndex"] = i

        return elements

    def _parse_element(self, node) -> dict | None:
        tag = etree.QName(node.tag).localname
        if tag not in ("Rectangle", "TextFrame", "Oval", "Polygon", "GraphicLine"):
            return None

        bounds_str = node.get("GeometricBounds", "")
        transform_str = node.get("ItemTransform", "")
        if not bounds_str:
            return None

        # Local bounds (relative to element origin)
        lx, ly, lw, lh = parse_bounds(bounds_str)
        # Transform (translation of origin in spread coords)
        tx, ty = parse_transform(transform_str)

        # Convert from spread coords (center-origin) to page coords (top-left)
        # spread origin = center of page
        half_w = self.page_w / 2
        half_h = self.page_h / 2

        # Element top-left in page space
        css_x = round((tx + lx) + half_w, 2)
        css_y = round((ty + ly) + half_h, 2)
        css_w = round(lw, 2)
        css_h = round(lh, 2)

        el = {
            "type": tag.lower(),
            "self": node.get("Self", ""),
            "x": css_x,
            "y": css_y,
            "width": css_w,
            "height": css_h,
            "zIndex": 0,
            "opacity": float(node.get("Opacity", 100)) / 100,
        }

        # Fill color
        fill_ref = node.get("FillColor", "")
        if fill_ref in self.colors:
            el["backgroundColor"] = self.colors[fill_ref]
        else:
            # Look for nested Color element
            for color_node in node.iter("Color"):
                cv = color_node.get("ColorValue", "")
                if cv:
                    el["backgroundColor"] = parse_color_value(cv)
                    break

        # Stroke
        stroke_color = node.get("StrokeColor", "")
        stroke_weight = node.get("StrokeWeight", "0")
        if stroke_color and float(stroke_weight) > 0:
            el["borderColor"] = self.colors.get(stroke_color, "#000000")
            el["borderWidth"] = pt_to_px(stroke_weight)

        # Corner radius (for rounded rectangles)
        corner_radius = node.get("CornerRadius", "0")
        if float(corner_radius) > 0:
            el["borderRadius"] = pt_to_px(corner_radius)

        # Text content
        if tag == "TextFrame":
            el["type"] = "text"
            story_ref = node.get("ParentStory", "")
            if story_ref in self.stories:
                el["paragraphs"] = self.stories[story_ref]

        # Image placeholder (Rectangle with linked image)
        if tag == "Rectangle":
            for image in node.iter("Image"):
                href = image.get("{http://www.w3.org/1999/xlink}href", "")
                if href:
                    el["type"] = "image"
                    el["src"] = href

        return el


def extract(idml_path: str, output_path: str = None) -> dict:
    """Main entry point. Returns layout dict and optionally saves JSON."""
    parser = IDMLParser(idml_path)
    layout = parser.parse()

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(layout, f, indent=2, ensure_ascii=False)
        print(f"Layout saved → {output_path}")

    return layout


if __name__ == "__main__":
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else "samples/sample_banner.idml"
    out = sys.argv[2] if len(sys.argv) > 2 else "output/layout.json"
    layout = extract(src, out)
    print(f"Canvas: {layout['canvas']['width']}x{layout['canvas']['height']}px")
    print(f"Elements: {len(layout['elements'])}")
    for el in layout['elements']:
        print(f"  [{el['type']:10s}] x={el['x']} y={el['y']} w={el['width']} h={el['height']}")
