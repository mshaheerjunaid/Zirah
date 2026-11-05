"""Write Board A on the DevEBox as one standalone SVG (Figure 7 of the technical report).

    python analysis/paper_figures/export_breadboard_svg.py   ->  breadboard_as_built.svg
"""
import re
import sys
from pathlib import Path

LAYOUT = Path(__file__).resolve().parents[2] / "hardware" / "layout"
sys.path.insert(0, str(LAYOUT))
import build_guide as g  # noqa: E402

css = (LAYOUT / "build_guide.css").read_text()
light = dict(re.findall(r"--([\w-]+):\s*([^;]+);", css.split("@media")[0]))
css = re.sub(r"var\(--([\w-]+)\)", lambda m: light.get(m.group(1), "").strip(), css)
css = re.sub(r"@media[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}", "", css)
svg = g.board_svg()
head = '<svg id="board" viewBox="0 0 %d %d" role="img" aria-label="Breadboard drawing">' % (g.W, g.H)
svg = svg.replace(head, '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" width="%dpt" height="%dpt">\n'
                  '<style>\n%s\n.part.later{display:inline}.ring{display:none}\n</style>\n'
                  '<rect x="0" y="0" width="%d" height="%d" fill="#ffffff"/>' % (g.W, g.H, g.W, g.H, css, g.W, g.H))
Path("breadboard_as_built.svg").write_text(svg)
print("wrote breadboard_as_built.svg")
