#!/usr/bin/env python3
"""
idml2banner — IDML → HTML5 IAB Banner Pipeline
================================================
Usage:
  python3 idml2banner.py <file.idml> [options]

Options:
  --sizes     Comma-separated IAB sizes to generate (default: source size only)
              e.g. --sizes 300x250,728x90,320x50
  --assets    Path to folder with linked images
  --click     Click URL (default: %%CLICK_URL_UNESC%%)
  --out       Output directory (default: ./output)
  --json-only Only extract JSON, skip rendering

Examples:
  python3 idml2banner.py banner_master.idml
  python3 idml2banner.py banner_master.idml --sizes 300x250,728x90,320x50
  python3 idml2banner.py banner_master.idml --assets ./links --click https://example.com
"""

import argparse
import json
import os
import sys
import shutil
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from extractor.idml_parser import extract
from renderer.html5_renderer import render, IAB_SIZES


def scale_layout(layout: dict, target_w: int, target_h: int) -> dict:
    """
    Scale a layout to a new canvas size.
    Uses proportional scaling with center-crop strategy:
      - Finds the best fit scale factor
      - Centers content within the new canvas
    """
    import copy
    src_w = layout["canvas"]["width"]
    src_h = layout["canvas"]["height"]

    if src_w == 0 or src_h == 0:
        return layout

    scale_x = target_w / src_w
    scale_y = target_h / src_h

    # Use the smaller scale to fit content, then center
    scale = min(scale_x, scale_y)
    offset_x = (target_w - src_w * scale) / 2
    offset_y = (target_h - src_h * scale) / 2

    scaled = copy.deepcopy(layout)
    scaled["canvas"] = {"width": float(target_w), "height": float(target_h)}

    for el in scaled["elements"]:
        el["x"]      = round(el["x"]      * scale + offset_x, 2)
        el["y"]      = round(el["y"]      * scale + offset_y, 2)
        el["width"]  = round(el["width"]  * scale, 2)
        el["height"] = round(el["height"] * scale, 2)

        # Scale font sizes
        for para in el.get("paragraphs", []):
            for run in para.get("runs", []):
                run["size"] = round(run["size"] * scale, 1)

        # Scale border width
        if "borderWidth" in el:
            el["borderWidth"] = round(el["borderWidth"] * scale, 1)

        # Scale border radius
        if "borderRadius" in el:
            el["borderRadius"] = round(el["borderRadius"] * scale, 1)

    return scaled


def parse_sizes(sizes_str: str) -> list[tuple[int, int]]:
    """Parse '300x250,728x90' → [(300,250),(728,90)]"""
    result = []
    for s in sizes_str.split(","):
        s = s.strip()
        if "x" in s:
            try:
                w, h = s.split("x")
                result.append((int(w), int(h)))
            except ValueError:
                print(f"  [warn] Invalid size format: {s} (expected WxH)")
    return result


def print_banner():
    print("""
╔══════════════════════════════════════════════╗
║          idml2banner  v0.1.0                 ║
║  IDML → HTML5 IAB Banner Generator          ║
╚══════════════════════════════════════════════╝
""")


def main():
    print_banner()

    parser = argparse.ArgumentParser(
        description="Convert InDesign IDML to HTML5 IAB banners",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("idml", help="Path to .idml file")
    parser.add_argument("--sizes",   default=None,
                        help="Target sizes e.g. 300x250,728x90,320x50")
    parser.add_argument("--assets",  default=None,
                        help="Directory with linked image assets")
    parser.add_argument("--click",   default="%%CLICK_URL_UNESC%%",
                        help="Click-through URL")
    parser.add_argument("--out",     default="output",
                        help="Output directory")
    parser.add_argument("--json-only", action="store_true",
                        help="Only extract JSON layout, skip HTML rendering")
    args = parser.parse_args()

    # ── Validate input ────────────────────────────────────
    if not os.path.exists(args.idml):
        print(f"✗  File not found: {args.idml}")
        sys.exit(1)

    os.makedirs(args.out, exist_ok=True)

    # ── Step 1: Extract ───────────────────────────────────
    print(f"[1/3] Extracting layout from {args.idml} ...")
    json_path = os.path.join(args.out, "layout.json")
    layout = extract(args.idml, json_path)

    src_w = int(layout["canvas"]["width"])
    src_h = int(layout["canvas"]["height"])
    size_name = IAB_SIZES.get((src_w, src_h), f"custom {src_w}x{src_h}")
    print(f"      Canvas: {src_w}×{src_h}px  [{size_name}]")
    print(f"      Elements: {len(layout['elements'])}")

    if args.json_only:
        print(f"\n✓  JSON saved → {json_path}")
        return

    # ── Step 2: Determine target sizes ───────────────────
    if args.sizes:
        targets = parse_sizes(args.sizes)
    else:
        targets = [(src_w, src_h)]  # Default: source size only

    print(f"\n[2/3] Rendering {len(targets)} banner(s)...")

    generated = []
    for target_w, target_h in targets:
        size_label = IAB_SIZES.get((target_w, target_h), f"{target_w}x{target_h}")
        print(f"      {target_w}×{target_h}  [{size_label}] ", end="", flush=True)

        # Scale layout if needed
        if (target_w, target_h) == (src_w, src_h):
            target_layout = layout
        else:
            target_layout = scale_layout(layout, target_w, target_h)

        # Save scaled JSON for this size
        scaled_json = os.path.join(args.out, f"layout_{target_w}x{target_h}.json")
        with open(scaled_json, "w") as f:
            json.dump(target_layout, f, indent=2)

        # Render to HTML5 zip
        zip_path = render(
            scaled_json,
            output_dir=args.out,
            assets_dir=args.assets,
            click_url=args.click,
        )
        generated.append((target_w, target_h, size_label, zip_path))
        print("✓")

    # ── Step 3: Summary ───────────────────────────────────
    print(f"\n[3/3] Summary")
    print(f"      {'Size':<12} {'Format':<22} {'File'}")
    print(f"      {'────':<12} {'──────':<22} {'────'}")
    for w, h, label, path in generated:
        fname = os.path.basename(path)
        fsize = os.path.getsize(path)
        print(f"      {w}×{h:<8} {label:<22} {fname}  ({fsize:,} bytes)")

    print(f"\n✓  Output directory: {os.path.abspath(args.out)}/")


if __name__ == "__main__":
    main()
