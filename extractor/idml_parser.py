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
                                    # Extract font family name
                                    font_nodes = csr.findall(".//Properties/AppliedFont")
                                    font_family = font_nodes[0].text.strip() if font_nodes else csr.get("AppliedFont", "")
                                    # Normalize: keep only family name, drop style suffix
                                    import re as _re
                                    font_family = _re.sub(r"\s+(Bold|Italic|Regular|Light|Medium|Black|Thin|Semibold|Condensed|Extra.*|Ultra.*).*$", "", font_family, flags=_re.IGNORECASE).strip()
                                    para["runs"].append({
                                        "text": text,
                                        "size": pt_to_px(size) if size else 12,
                                        "color": self.colors.get(fill, "#000000"),
                                        "bold": "Bold" in font_style,
                                        "italic": "Italic" in font_style,
                                        "fontFamily": font_family,
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
                        from lxml.etree import QName as _QName
                        tag = _QName(child.tag).localname
                        # Convert pure vector groups to SVG elements
                        if tag == 'Group':
                            vgroups = _find_vector_groups(child)
                            if vgroups:
                                for vg, _ in vgroups:
                                    svg_el = group_to_svg_element(vg, self.colors, self.page_tx, self.page_ty)
                                    if svg_el:
                                        elements.append(svg_el)
                                # Also parse non-vector children
                                for gchild in child:
                                    if callable(gchild.tag): continue
                                    if not _find_vector_groups(gchild):
                                        els = self._parse_element_recursive(gchild)
                                        elements.extend(els)
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


# ── SVG Group extraction ──────────────────────────────────

def _parse_transform_full(transform_str):
    """Returns full 6-tuple (a,b,c,d,tx,ty)."""
    try:
        p = [float(x) for x in transform_str.strip().split()]
        if len(p) == 6:
            return (p[0],p[1],p[2],p[3],p[4],p[5])
    except Exception:
        pass
    return (1,0,0,1,0,0)

def _compose_transform(t1, t2):
    """Compose two affine transforms: apply t1 first, then t2."""
    a1,b1,c1,d1,tx1,ty1 = t1
    a2,b2,c2,d2,tx2,ty2 = t2
    return (
        a2*a1 + c2*b1, b2*a1 + d2*b1,
        a2*c1 + c2*d1, b2*c1 + d2*d1,
        a2*tx1 + c2*ty1 + tx2,
        b2*tx1 + d2*ty1 + ty2,
    )

def _apply_t(pts, t):
    a,b,c,d,tx,ty = t
    return [(a*x+c*y+tx, b*x+d*y+ty) for x,y in pts]

def _has_text_or_image(node):
    for c in node.iter():
        if callable(c.tag): continue
        from lxml.etree import QName
        if QName(c.tag).localname in ('TextFrame','Image'): return True
    return False

def _get_poly_count(node):
    from lxml.etree import QName
    return sum(1 for c in node.iter()
               if not callable(c.tag) and QName(c.tag).localname in ('Polygon','Rectangle'))

def _extract_shapes(node, cumulative_t, colors):
    """Recursively extract polygon/rect shapes with fully composed transforms."""
    from lxml.etree import QName
    shapes = []
    local_t = _parse_transform_full(node.get('ItemTransform','1 0 0 1 0 0'))
    total_t = _compose_transform(local_t, cumulative_t)
    tag = QName(node.tag).localname

    if tag in ('Polygon','Rectangle'):
        fill_ref = node.get('FillColor','')
        fill = '#ffffff' if fill_ref == 'Color/Paper' else colors.get(fill_ref, '#000000')
        pts = []
        for p in node.iter('PathPointType'):
            a = p.get('Anchor','')
            if a:
                try:
                    x,y = float(a.split()[0]), float(a.split()[1])
                    pts.append((x,y))
                except ValueError:
                    pass
        if len(pts) >= 2:
            shapes.append({'pts': _apply_t(pts, total_t), 'fill': fill})
    else:
        for child in node:
            if not callable(child.tag):
                shapes.extend(_extract_shapes(child, total_t, colors))
    return shapes

def _find_vector_groups(node, depth=0):
    """Find groups containing only vector shapes."""
    from lxml.etree import QName
    if callable(node.tag): return []
    tag = QName(node.tag).localname
    results = []
    if tag == 'Group':
        if not _has_text_or_image(node) and _get_poly_count(node) > 2:
            results.append((node, depth))
            return results
        for child in node:
            results.extend(_find_vector_groups(child, depth+1))
    return results

def group_to_svg_element(group_node, colors, page_tx, page_ty):
    """Convert a vector Group to an svg_group layout element."""
    parent_t = (1,0,0,1,0,0)
    shapes = _extract_shapes(group_node, parent_t, colors)
    if not shapes: return None

    # Convert spread coords → page coords
    shapes_page = [{'pts': [(x-page_tx, y-page_ty) for x,y in s['pts']],
                    'fill': s['fill']} for s in shapes]

    all_pts = [p for s in shapes_page for p in s['pts']]
    xs = [p[0] for p in all_pts]
    ys = [p[1] for p in all_pts]
    min_x, min_y = min(xs), min(ys)
    vw = round(max(xs) - min_x, 2)
    vh = round(max(ys) - min_y, 2)

    paths = []
    for s in shapes_page:
        pts_norm = [(x-min_x, y-min_y) for x,y in s['pts']]
        d = 'M ' + ' L '.join(f'{x:.2f},{y:.2f}' for x,y in pts_norm) + ' Z'
        paths.append(f'<path d="{d}" fill="{s["fill"]}"/>')

    return {
        'type': 'svg_group',
        'self': group_node.get('Self',''),
        'x': round(min_x, 2),
        'y': round(min_y, 2),
        'width': vw,
        'height': vh,
        'zIndex': 0,
        'opacity': 1.0,
        'viewBox': f'0 0 {vw} {vh}',
        'paths': paths,
    }
