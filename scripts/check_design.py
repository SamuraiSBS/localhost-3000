"""Offline integrity checks for the portable design assets, not browser acceptance."""
import hashlib
import json
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit


class PageLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids, self.targets, self.labels = [], [], []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "id" in values:
            self.ids.append(values["id"])
        if tag == "label" and "for" in values:
            self.labels.append(values["for"])
        for attribute in ("src", "href"):
            if attribute in values:
                # Source/license links are navigation, not remote runtime assets.
                if tag == "a" and attribute == "href" and values[attribute].startswith("https://"):
                    continue
                self.targets.append(values[attribute])
        if "srcset" in values:
            self.targets.extend(item.strip().split()[0] for item in values["srcset"].split(","))


def check_design(root):
    design = root / "design"
    if not design.exists():
        return
    for page in design.glob("*.html"):
        parser = PageLinks()
        parser.feed(page.read_text(encoding="utf-8"))
        assert len(set(parser.ids)) == len(parser.ids), f"Duplicate id: {page.name}"
        assert all(label in parser.ids for label in parser.labels), f"Unbound label: {page.name}"
        for target in parser.targets:
            if target.startswith("data:"):
                continue
            assert "://" not in target, f"Unexpected external runtime dependency: {page.name}"
            parsed_target = urlsplit(target)
            file, anchor = parsed_target.path, parsed_target.fragment
            assert (page.parent / file).exists(), f"Missing asset: {page.name}: {target}"
            if not file and anchor:
                assert anchor in parser.ids, f"Missing anchor: {target}"
    css_files = list(design.glob("*.css"))
    css = "\n".join(file.read_text(encoding="utf-8") for file in css_files)
    tokens = set(re.findall(r"(--[a-z][a-z0-9-]*)\s*:", (design / "tokens.css").read_text(encoding="utf-8")))
    assert set(re.findall(r"var\((--[a-z][a-z0-9-]*)", css)) <= tokens, "Unknown design token"
    for file in css_files:
        for target in re.findall(r'url\(["\']?([^"\')]+)', file.read_text(encoding="utf-8")):
            assert (file.parent / target).is_file(), f"Missing CSS asset: {target}"
    fonts = json.loads((design / "assets/fonts/sources.json").read_text(encoding="utf-8"))
    art = json.loads((design / "assets/columns.provenance.json").read_text(encoding="utf-8"))
    for folder, entries in [(design / "assets/fonts", fonts["outputs"]),
                            (design / "assets", fonts["wordmarks"]),
                            (design / "assets", art["artifacts"])]:
        for name, expected in entries.items():
            content = (folder / name).read_bytes()
            assert len(content) == expected["bytes"], f"Size mismatch: {name}"
            assert hashlib.sha256(content).hexdigest() == expected["sha256"], f"Hash mismatch: {name}"
    for name in fonts["outputs"]:
        signature = b"wOF2" if name.endswith(".woff2") else b"wOFF"
        assert (design / "assets/fonts" / name).read_bytes()[:4] == signature, f"Invalid webfont: {name}"
    assert sum(item["bytes"] for item in fonts["outputs"].values()) <= 200_000, "Font budget exceeded"
    assert art["artifacts"]["columns-768.webp"]["bytes"] <= 500_000, "Mobile art budget exceeded"
    glass = json.loads((design / "tooling/aceternity-liquid-glass.json").read_text(encoding="utf-8"))
    icons = json.loads((design / "assets/icons/sources.json").read_text(encoding="utf-8"))
    for name, expected in icons["outputs"].items():
        content = (design / "assets/icons" / name).read_bytes()
        assert len(content) == expected["bytes"] and hashlib.sha256(content).hexdigest() == expected["sha256"], f"Icon provenance mismatch: {name}"
    assert hashlib.sha256((design / "liquid-glass-optics.js").read_bytes()).hexdigest() == glass["optics_sha256"], "Liquid Glass optics provenance mismatch"
    print("Design asset hashes, local links, font headers, tokens and mobile/font budgets passed.")


if __name__ == "__main__":
    check_design(Path(__file__).resolve().parents[1])
