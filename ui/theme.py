"""Design tokens, fonts and the style sheet of the Zirah desktop app.

Palette (fixed by the project brief):
    Obsidian #080B10  window and instrument glass
    Graphite #0E141C  faceplate panels
    Slate    #141D28  raised controls, inputs, tracks
    Border   #243140  seams between regions
    Cyan     #59E1E8  live signal, active stage, primary action
    Blue     #668CFF  verification and trust
    Ice      #EAF2F7  primary text
    Steel    #8796A8  secondary text, units
    Success  #55D6A0, Warning #E8B65C, Error #FF6474

Type: Chakra Petch for display text and big readouts, IBM Plex Sans for the
interface, IBM Plex Mono only for data (hashes, fingerprints, sample codes).
All three are bundled under the SIL Open Font License (ui/fonts).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QFontDatabase

HERE = Path(__file__).resolve().parent

OBSIDIAN = "#080B10"
GRAPHITE = "#0E141C"
SLATE = "#141D28"
BORDER = "#243140"
CYAN = "#59E1E8"
BLUE = "#668CFF"
ICE = "#EAF2F7"
STEEL = "#8796A8"
SUCCESS = "#55D6A0"
WARNING = "#E8B65C"
ERROR = "#FF6474"


def qc(hex_color: str, alpha: float = 1.0) -> QColor:
    c = QColor(hex_color)
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def mix(a: str, b: str, t: float) -> QColor:
    ca, cb = QColor(a), QColor(b)
    return QColor(round(ca.red() + (cb.red() - ca.red()) * t),
                  round(ca.green() + (cb.green() - ca.green()) * t),
                  round(ca.blue() + (cb.blue() - ca.blue()) * t))


# Motion. Every animation reads these, so Reduce Motion (and the system's
# own setting) turns them off in one place.
class Motion:
    reduce = False

    @classmethod
    def ms(cls, value: int) -> int:
        return 0 if cls.reduce else value


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------

DISPLAY = "Chakra Petch"
SANS = "IBM Plex Sans"
MONO = "IBM Plex Mono"

_loaded = False


def load_fonts() -> None:
    global _loaded
    if _loaded:
        return
    for f in ("ChakraPetch-Medium.ttf", "ChakraPetch-SemiBold.ttf", "IBMPlexSans-Variable.ttf",
              "IBMPlexMono-Regular.ttf", "IBMPlexMono-Medium.ttf"):
        QFontDatabase.addApplicationFont(str(HERE / "fonts" / f))
    _loaded = True


def font(role: str) -> QFont:
    """Fonts by role. Sizes are pixels so the layout scales with the DPI."""
    spec = {
        "digest": (DISPLAY, 44, 600, 2.0),
        "display": (DISPLAY, 26, 600, 0.5),
        "title": (DISPLAY, 17, 600, 0.3),
        "lamp": (DISPLAY, 19, 600, 0.3),
        "stage": (DISPLAY, 12, 600, 0.6),
        "readout": (DISPLAY, 20, 500, 0.4),
        "body": (SANS, 14, 400, 0.0),
        "body_strong": (SANS, 14, 600, 0.0),
        "small": (SANS, 12, 400, 0.1),
        "small_strong": (SANS, 12, 600, 0.2),
        "label": (SANS, 12, 500, 0.2),
        "button": (SANS, 13, 600, 0.2),
        "tab": (SANS, 14, 600, 0.2),
        "mono": (MONO, 13, 400, 0.0),
        "mono_small": (MONO, 11, 400, 0.0),
        "mono_big": (MONO, 18, 500, 0.5),
    }[role]
    fam, px, weight, spacing = spec
    f = QFont(fam)
    f.setPixelSize(px)
    f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    f.setWeight(QFont.Weight(weight))
    if fam == SANS:
        # IBM Plex Sans ships as a variable font: set its weight axis directly.
        try:
            f.setVariableAxis(QFont.Tag("wght"), float(weight))
        except (AttributeError, TypeError):
            pass
    f.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return f


# ---------------------------------------------------------------------------
# Strings (all interface copy, Title Case, checked by tests/style/title_case.py)
# ---------------------------------------------------------------------------

_strings = json.loads((HERE / "strings.json").read_text(encoding="utf-8"))


def T(key: str, **fmt) -> str:
    node = _strings
    for part in key.split("."):
        node = node.get(part) if isinstance(node, dict) else None
        if node is None:
            return key
    if not isinstance(node, str):
        return key
    if fmt:
        return re.sub(r"\{(\w+)\}", lambda m: str(fmt.get(m.group(1), m.group(0))), node)
    return node


def has(key: str) -> bool:
    node = _strings
    for part in key.split("."):
        if not isinstance(node, dict) or part not in node:
            return False
        node = node[part]
    return True


def error_text(err: dict | None) -> tuple[str, str]:
    code = (err or {}).get("code") or "DEFAULT"
    if has(f"errors.{code}.title"):
        return T(f"errors.{code}.title"), T(f"errors.{code}.hint")
    return T("errors.DEFAULT.title"), T("errors.DEFAULT.hint", code=code)


# ---------------------------------------------------------------------------
# Style sheet. Colours and borders only; fonts are set in code because the
# variable Plex Sans weight cannot be expressed in a style sheet.
# ---------------------------------------------------------------------------

QSS = f"""
* {{ outline: none; }}
QWidget {{ color: {ICE}; background: transparent; }}
QMainWindow, #Root {{ background: {OBSIDIAN}; }}
#Spine {{ background: {GRAPHITE}; border-right: 1px solid {BORDER}; }}
#Deck {{ background: {OBSIDIAN}; }}
#Seam {{ background: {BORDER}; }}
#Panel {{ background: {GRAPHITE}; }}
#Cell {{ background: {GRAPHITE}; }}
#Rack {{ background: {BORDER}; border-radius: 2px; }}
#Chosen {{ background: rgba(20, 29, 40, 0.7); border: 1px solid {BORDER}; border-radius: 10px; }}
QLabel {{ background: transparent; }}
QLabel[role="muted"] {{ color: {STEEL}; }}
QLabel[role="cyan"] {{ color: {CYAN}; }}
QLabel[role="blue"] {{ color: {BLUE}; }}
QLabel[role="ok"] {{ color: {SUCCESS}; }}
QLabel[role="warn"] {{ color: {WARNING}; }}
QLabel[role="err"] {{ color: {ERROR}; }}
QLineEdit {{
    background: {SLATE}; border: 1px solid {BORDER}; border-radius: 6px;
    padding: 8px 10px; color: {ICE}; selection-background-color: {CYAN}; selection-color: {OBSIDIAN};
}}
QLineEdit:focus {{ border: 1px solid {CYAN}; }}
QPlainTextEdit {{
    background: {OBSIDIAN}; border: 1px solid {BORDER}; border-radius: 6px; color: {STEEL};
    padding: 8px; selection-background-color: {CYAN}; selection-color: {OBSIDIAN};
}}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {BORDER}; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: {STEEL}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QScrollBar:horizontal {{ height: 0; }}
QToolTip {{ background: {SLATE}; color: {ICE}; border: 1px solid {BORDER}; padding: 6px; }}
QProgressBar {{ background: {SLATE}; border: none; border-radius: 3px; height: 6px; }}
QProgressBar::chunk {{ background: {CYAN}; border-radius: 3px; }}
QMessageBox {{ background: {GRAPHITE}; }}
"""
