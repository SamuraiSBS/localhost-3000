"""Build local WOFF2 fonts and an outlined wordmark; no application dependency.

Requires design/tooling/requirements.txt. Download inputs from the pinned URLs in
design/assets/fonts/sources.json into --source-dir before rebuilding.
"""
import argparse
import hashlib
import json
import re
import shutil
import sys
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=Path(".state/design-sources/console-v2"))
    parser.add_argument("--tool-path", type=Path)
    parser.add_argument("--fetch", action="store_true", help="Fetch the pinned public font inputs and verify their recorded hashes")
    parser.add_argument("--direct", action="store_true", help="Use a process-local direct connection for public input downloads")
    args = parser.parse_args()
    if args.tool_path:
        sys.path.insert(0, str(args.tool_path.resolve()))
    from fontTools.pens.svgPathPen import SVGPathPen
    from fontTools.ttLib import TTFont
    from fontTools.varLib.instancer import instantiateVariableFont

    root = Path(__file__).resolve().parents[1]
    assets = root / "design/assets"
    fonts = assets / "fonts"
    fonts.mkdir(parents=True, exist_ok=True)
    if args.fetch:
        pinned = json.loads((fonts / "sources.json").read_text(encoding="utf-8"))["sources"]
        args.source_dir.mkdir(parents=True, exist_ok=True)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if args.direct else urllib.request.build_opener()
        for name, data in pinned.items():
            if not data["url"].startswith("https://raw.githubusercontent.com/google/fonts/"):
                raise ValueError("Unexpected source host")
            with opener.open(data["url"], timeout=30) as response:
                raw = response.read()
            if hashlib.sha256(raw).hexdigest() != data["sha256"]:
                raise ValueError(f"Source checksum mismatch: {name}")
            (args.source_dir / name).write_bytes(raw)
        (args.source_dir / "sources.json").write_text(json.dumps(pinned), encoding="utf-8")
    sources = json.loads((args.source_dir / "sources.json").read_text(encoding="utf-8"))
    outputs = {}
    for source_name, output_name in [
        ("JetBrainsMono.ttf", "jetbrains-mono-variable.woff2"),
    ]:
        source = args.source_dir / source_name
        font = TTFont(source)
        font.recalcTimestamp = False
        assert all(ord(char) in font.getBestCmap() for char in "ЁёЯяАаlocalhost3000"), source_name
        font.flavor = "woff2"
        output = fonts / output_name
        font.save(output)
        outputs[output_name] = {"source": source_name, "bytes": output.stat().st_size,
                                "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}
        sources[source_name]["sha256"] = hashlib.sha256(source.read_bytes()).hexdigest()
    for name in ["JetBrainsMono-OFL.txt"]:
        shutil.copyfile(args.source_dir / name, fonts / name)
        sources[name]["sha256"] = hashlib.sha256((fonts / name).read_bytes()).hexdigest()
    (fonts / "sources.json").write_text(
        json.dumps({"sources": sources, "outputs": outputs}, indent=2) + "\n", encoding="utf-8")

    font = instantiateVariableFont(TTFont(args.source_dir / "JetBrainsMono.ttf"), {"wght": 600})
    glyphs, cmap = font.getGlyphSet(), font.getBestCmap()
    cursor, paths, starts = 40, [], {}
    for index, char in enumerate("localhost3000"):
        name = cmap[ord(char)]
        pen = SVGPathPen(glyphs, ntos=lambda value: f"{value:.2f}".rstrip("0").rstrip("."))
        glyphs[name].draw(pen)
        paths.append(f'<path transform="translate({cursor} 820) scale(1 -1)" d="{pen.getCommands()}"/>')
        starts[index] = cursor
        cursor += font["hmtx"].metrics[name][0] - 5
    width = int(cursor + 40)
    css = (root / "design/tokens.css").read_text(encoding="utf-8")
    ink = re.search(r"--color-text:\s*(#[a-fA-F0-9]+)", css).group(1)
    clean = "\n".join(paths)
    cuts = (f'<rect x="{starts[8]+290}" y="475" width="90" height="12"/>'
            f'<rect x="{starts[10]+460}" y="210" width="28" height="42"/>'
            f'<rect x="{starts[12]+130}" y="754" width="72" height="15"/>')
    for compact in (False, True):
        decoration = "" if compact else (
            f'<defs><mask id="erosion" maskUnits="userSpaceOnUse" x="0" y="0" width="{width}" height="950">'
            f'<rect width="{width}" height="950" fill="white"/><g fill="black">{cuts}</g></mask></defs>')
        mask = "" if compact else ' mask="url(#erosion)"'
        fragments = "" if compact else (
            f'<path d="M{starts[9]+510} 816h16v18h-16z" fill="{ink}"/>')
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} 950" role="img" aria-labelledby="name">'
               '<title id="name">localhost3000</title>' + decoration +
               f'<g fill="{ink}"{mask}>{clean}</g>' + fragments + '</svg>\n')
        (assets / ("wordmark-compact.svg" if compact else "wordmark.svg")).write_text(svg, encoding="utf-8")
    print(f"WOFF2: {sum(item['bytes'] for item in outputs.values())} bytes; Cyrillic verified; wordmarks outlined.")


if __name__ == "__main__":
    main()
