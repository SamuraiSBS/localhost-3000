"""Verify the official local font and export outlined wordmarks.

Requires design/tooling/requirements.txt only at build time. The webfont is copied
unchanged; no subsetting or conversion of the proprietary font is performed.
"""
import argparse
import hashlib
import json
import re
import shutil
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urlparse


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=Path(".state/design-sources/neue-machina-v1"))
    parser.add_argument("--tool-path", type=Path)
    parser.add_argument("--fetch", action="store_true", help="Fetch the pinned official specimen font")
    parser.add_argument("--direct", action="store_true", help="Use a process-local direct connection")
    args = parser.parse_args()
    if args.tool_path:
        sys.path.insert(0, str(args.tool_path.resolve()))
    from fontTools.pens.boundsPen import BoundsPen
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont

    root = Path(__file__).resolve().parents[1]
    assets = root / "design/assets"
    fonts = assets / "fonts"
    manifest_path = fonts / "sources.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    source_name = "PPNeueMachina-InktrapVariable.woff"
    expected = manifest["sources"][source_name]
    args.source_dir.mkdir(parents=True, exist_ok=True)
    source = args.source_dir / source_name
    if args.fetch:
        url = urlparse(expected["url"])
        if url.scheme != "https" or url.hostname != "pangrampangram.com" or url.path != f"/cdn/shop/files/{source_name}":
            raise ValueError("Unexpected source URL")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if args.direct else urllib.request.build_opener()
        with opener.open(expected["url"], timeout=30) as response:
            raw = response.read()
        if hashlib.sha256(raw).hexdigest() != expected["sha256"]:
            raise ValueError("Source checksum mismatch")
        source.write_bytes(raw)
    raw = source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected["sha256"]:
        raise ValueError("Source checksum mismatch")
    font = TTFont(source)
    required = set(range(0x410, 0x450)) | {0x401, 0x451} | set(map(ord, "localhost3000"))
    missing = required - font.getBestCmap().keys()
    if missing:
        raise ValueError(f"Missing required codepoints: {sorted(missing)}")
    # The official URL ends in .woff; its signature is actually WOFF2.
    if raw[:4] != b"wOF2":
        raise ValueError("Expected a WOFF2 payload")
    output_name = "pp-neue-machina-inktrap-variable.woff2"
    shutil.copyfile(source, fonts / output_name)
    manifest["outputs"] = {output_name: {"source": source_name, "bytes": len(raw), "sha256": expected["sha256"]}}

    # Preserve the existing SVG frame and CSS sizes; fit the new glyphs uniformly.
    font = instantiateVariableFont(font, {"wght": 600, "ital": 0})
    glyphs, cmap = font.getGlyphSet(), font.getBestCmap()
    cursor, paths, bounds = 0, [], []
    for char in "localhost3000":
        glyph = glyphs[cmap[ord(char)]]
        pen = SVGPathPen(glyphs, ntos=lambda value: f"{value:.2f}".rstrip("0").rstrip("."))
        glyph.draw(pen)
        paths.append(f'<path transform="translate({cursor} 0)" d="{pen.getCommands()}"/>')
        bound_pen = BoundsPen(glyphs)
        glyph.draw(bound_pen)
        x0, y0, x1, y1 = bound_pen.bounds
        bounds.append((cursor + x0, y0, cursor + x1, y1))
        cursor += glyph.width
    x0 = min(box[0] for box in bounds)
    y0 = min(box[1] for box in bounds)
    x1 = max(box[2] for box in bounds)
    y1 = max(box[3] for box in bounds)
    width, height = 7815, 950
    scale = min((width - 80) / (x1 - x0), 800 / (y1 - y0))
    tx = (width - (x1 - x0) * scale) / 2 - x0 * scale
    ty = height / 2 + (y1 + y0) * scale / 2
    css = (root / "design/tokens.css").read_text(encoding="utf-8")
    ink = re.search(r"--color-text:\s*(#[a-fA-F0-9]+)", css).group(1)
    clean = "\n".join(paths)
    cuts = []
    for index, vertical in [(8, 0.48), (10, 0.18), (12, 0.82)]:
        left, bottom, right, top = bounds[index]
        cuts.append(f'<rect x="{tx + (left + (right-left)*0.55)*scale:.2f}" '
                    f'y="{ty - top*scale + (top-bottom)*scale*vertical:.2f}" width="65" height="12"/>')
    mark_left, _, mark_right, _ = bounds[9]
    fragment_x = tx + (mark_left + (mark_right-mark_left)*0.8) * scale
    for compact in (False, True):
        decoration = "" if compact else (
            f'<defs><mask id="erosion" maskUnits="userSpaceOnUse" x="0" y="0" width="{width}" height="{height}">'
            f'<rect width="{width}" height="{height}" fill="white"/><g fill="black">{"".join(cuts)}</g></mask></defs>')
        mask = "" if compact else ' mask="url(#erosion)"'
        fragments = "" if compact else f'<path d="M{fragment_x:.2f} 880h16v18h-16z" fill="{ink}"/>'
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-labelledby="name">'
               '<title id="name">localhost3000</title>' + decoration +
               f'<g{mask}><g fill="{ink}" transform="translate({tx:.4f} {ty:.4f}) scale({scale:.6f} {-scale:.6f})">'
               + clean + '</g></g>' + fragments + '</svg>\n')
        (assets / ("wordmark-compact.svg" if compact else "wordmark.svg")).write_text(svg, encoding="utf-8")
    manifest["wordmarks"] = {
        name: {"font_source": source_name, "weight": 600, "bytes": (assets / name).stat().st_size,
               "sha256": hashlib.sha256((assets / name).read_bytes()).hexdigest()}
        for name in ("wordmark.svg", "wordmark-compact.svg")
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Official WOFF2: {len(raw)} bytes; all Russian letters verified; weight-600 wordmarks exported.")


if __name__ == "__main__":
    main()
