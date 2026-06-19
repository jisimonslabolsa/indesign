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


NS = {
    "idPkg": "http://ns.adobe.com/AdobeInDesign/idml/1.0/packaging",
}

def pt_to_px(pt):
    return round(float(pt), 2)


def parse_color_value(color_value_str, space="RGB"):
    try:
        parts = [int(float(x)) for x in color_value_str.strip().split()]
        if space == "RGB" and len(parts) == 3:
            return "#{:02x}{:02x}{:02x}".format(*parts)
        if space == "CMYK" and len(parts) == 4:
            c, m, y, k = [x / 100 for x in parts]
            r = int(255 * (1 - c) * (1 - k))
            g = int(255 * (1 - m) * (1 - k))
            b = int(255 * (1 - y) * (1 - k))
            return "#{:02x}{:02x}{:02x}".format(r, g, b)
        if len(parts) == 3:
            return "#{:02x}{:02x}{:02x}".format(*parts)
    except Exception:
        pass
    return None


def parse_transform(transform_str):
    try:
        parts = [float(x) for x in transform_str.strip().split()]
        if len(parts) == 6:
            return parts[4], parts[5]
    except Exception:
        pass
    return 0.0, 0.0


def parse_bounds(bounds_str):
    try:
        y1, x1, y2, x2 = [float(v) for v in bounds_str.strip().split()]
        return x1, y1, x2 - x1, y2 - y1
    except Exception:
        return 0.0, 0.0, 0.0, 0.0


def bounds_from_path_points(node):
    """Extract bounding box from PathPointType anchors when GeometricBounds is absent."""
    points = []
    for pt in node.iter("PathPointType"):
        anchor = pt.get("Anchor", "")
        if anchor:
            try:
                x, y = [float(v) for v in anchor.strip().split()]
                points.append((x, y))
            except ValueError:
                pass
    if len(points) < 2:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    lx, ly = min(xs), min(ys)
    lw, lh = max(xs) - lx, max(ys) - ly
    return lx, ly, lw, lh


class IDMLParser:
    def __init__(self, idml_path: str):
        self.idml_path = idml_path
        self.zf = zipfile.ZipFile(idml_path, "r")
        self.colors = {}
        self.stories = {}
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

    def _parse_styles(self):
        try:
            xml = self.zf.read("Resources/Styles.xml")
            root = etree.fromstring(xml, etree.XMLParser(huge_tree=True))
            for color in root.iter("Color"):
                self_id = color.get("Self", "")
                cv = color.get("ColorValue", "")
                space = color.get("Space", "RGB")
                if cv:
                    hex_val = parse_color_value(cv, space)
                    if hex_val:
                        self.colors[self_id] = hex_val
                        name = color.get("Name", "")
                        if name:
                            self.colors[f"Color/{name}"] = hex_val
        except Exception as e:
            print(f"  [warn] styles: {e}")

        # Also parse Graphic.xml for additional swatches
        try:
            xml = self.zf.read("Resources/Graphic.xml")
            root = etree.fromstring(xml, etree.XMLParser(huge_tree=True))
            for color in root.iter("Color"):
                self_id = color.get("Self", "")
                cv = color.get("ColorValue", "")
                space = color.get("Space", "RGB")
                if cv and self_id not in self.colors:
                    hex_val = parse_color_value(cv, space)
                    if hex_val:
                        self.colors[self_id] = hex_val
                        name = color.get("Name", "")
                        if name:
                            self.colors[f"Color/{name}"] = hex_val
        except Exception:
            pass

    def _parse_stories(self):
        for name in self.zf.namelist():
            if name.startswith("Stories/") and name.endswith(".xml"):
                try:
                    xml = self.zf.read(name)
                    root = etree.fromstring(xml, etree.XMLParser(huge_tree=True))
                    for story in root.iter("Story"):
                        self_id = story.get("Self", "")
                        paragraphs = []
                        for psr in story.iter("ParagraphStyleRange"):
                            para = {"style": psr.get("AppliedParagraphStyle", ""), "runs": []}
                            for csr in psr.iter("CharacterStyleRange"):
                                size = csr.get("PointSize")
                                fill = csr.get("FillColor", "")
                                font_style = csr.get("FontStyle", "Regular")
                                content_nodes = csr.findall("Content")
                                text = "".join((n.text or "") for n in content_nodes)
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

    def _parse_spreads(self) -> list:
        elements = []
        # Only parse first spread (first page of master)
        spread_files = sorted([n for n in self.zf.namelist()
                                if n.startswith("Spreads/") and n.endswith(".xml")])
        for name in spread_files[:1]:
            try:
                xml = self.zf.read(name)
                root = etree.fromstring(xml, etree.XMLParser(huge_tree=True))
                for spread in root.iter("Spread"):
                    for page in spread.iter("Page"):
                        bounds_str = page.get("GeometricBounds", "")
                        transform_str = page.get("ItemTransform", "")
                        if bounds_str:
                            _, _, self.page_w, self.page_h = parse_bounds(bounds_str)
                            self.page_w = pt_to_px(self.page_w)
                            self.page_h = pt_to_px(self.page_h)
                        # Page ItemTransform gives us the page offset in spread
                        if transform_str:
                            self.page_tx, self.page_ty = parse_transform(transform_str)
                        break

                    for child in spread:
                        if callable(child.tag):
                            continue
                        els = self._parse_element_recursive(child)
                        elements.extend(els)

            except Exception as e:
                print(f"  [warn] spread {name}: {e}")

        for i, el in enumerate(elements):
            el["zIndex"] = i

        return elements

    def _parse_element_recursive(self, node, depth=0) -> list:
        """Parse element and recurse into Groups."""
        if callable(node.tag):
            return []

        tag = etree.QName(node.tag).localname

        if tag == "Group":
            results = []
            for child in node:
                results.extend(self._parse_element_recursive(child, depth + 1))
            return results

        el = self._parse_element(node)
        return [el] if el else []

    def _parse_element(self, node) -> dict | None:
        tag = etree.QName(node.tag).localname
        if tag not in ("Rectangle", "TextFrame", "Oval", "Polygon"):
            return None

        transform_str = node.get("ItemTransform", "")

        # Bounds: try GeometricBounds first, then PathGeometry
        bounds_str = node.get("GeometricBounds", "")
        if bounds_str:
            lx, ly, lw, lh = parse_bounds(bounds_str)
        else:
            result = bounds_from_path_points(node)
            if result is None:
                return None
            lx, ly, lw, lh = result

        if lw <= 0 or lh <= 0:
            return None

        tx, ty = parse_transform(transform_str)

        # Page offset: InDesign stores page at (page_tx, page_ty) in spread
        # ItemTransform tx/ty is relative to spread center
        # page_tx/ty is the page's top-left corner offset in spread coords
        page_tx = getattr(self, 'page_tx', -(self.page_w / 2))
        page_ty = getattr(self, 'page_ty', -(self.page_h / 2))

        # Convert to page-relative CSS coords (top-left origin)
        css_x = round((tx + lx) - page_tx, 2)
        css_y = round((ty + ly) - page_ty, 2)
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

        # Fill color — try direct attribute, then nested Color element
        fill_ref = node.get("FillColor", "")
        if fill_ref in self.colors:
            el["backgroundColor"] = self.colors[fill_ref]
        elif fill_ref == "Color/Paper":
            el["backgroundColor"] = "#ffffff"
        else:
            for color_node in node.iter("Color"):
                cv = color_node.get("ColorValue", "")
                space = color_node.get("Space", "RGB")
                if cv:
                    hex_val = parse_color_value(cv, space)
                    if hex_val:
                        el["backgroundColor"] = hex_val
                        break

        # Stroke
        stroke_color = node.get("StrokeColor", "")
        stroke_weight = node.get("StrokeWeight", "0")
        try:
            if float(stroke_weight) > 0 and stroke_color in self.colors:
                el["borderColor"] = self.colors[stroke_color]
                el["borderWidth"] = pt_to_px(stroke_weight)
        except ValueError:
            pass

        # Border radius
        corner_radius = node.get("CornerRadius", "0")
        try:
            if float(corner_radius) > 0:
                el["borderRadius"] = pt_to_px(corner_radius)
        except ValueError:
            pass

        # Text
        if tag == "TextFrame":
            el["type"] = "text"
            story_ref = node.get("ParentStory", "")
            if story_ref in self.stories:
                el["paragraphs"] = self.stories[story_ref]

        # Image
        if tag == "Rectangle":
            for image in node.iter("Image"):
                href = image.get("{http://www.w3.org/1999/xlink}href", "")
                if href:
                    el["type"] = "image"
                    el["src"] = href
                    break

        return el


def extract(idml_path: str, output_path: str = None) -> dict:
    parser = IDMLParser(idml_path)
    layout = parser.parse()
    if output_path:
        os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
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
        bg = el.get('backgroundColor', '')
        text = ''
        if el.get('paragraphs'):
            text = el['paragraphs'][0]['runs'][0]['text'][:30] if el['paragraphs'][0]['runs'] else ''
        print(f"  [{el['type']:10s}] x={el['x']:7.1f} y={el['y']:7.1f} w={el['width']:7.1f} h={el['height']:7.1f}  {bg}  {text}")
