"""Shared helpers for napari talk plots.

    from napari_plots import use, COLORS, CONTEXT, save
    use("dark")            # or "light"
    ...
    save(fig, "plots/contributors")   # writes -dark/-light .svg + .png next to the slides
"""
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib import font_manager

HERE = Path(__file__).parent
# register the theme's own font files so plots match the slides on any machine
for _f in (HERE.parents[1] / "_extensions" / "napari" / "fonts").glob("*.ttf"):
    font_manager.fontManager.addfont(str(_f))
COLORS = {
    "dark": ["#009bf2", "#b98a14", "#8f72d4", "#1f9e80"],
    "light": ["#0074b8", "#b8860b", "#6a4fb0", "#1f8a70"],
}
CONTEXT = {"dark": "#565f6c", "light": "#b4b2af"}   # everything that is not the story
INK = {"dark": "#f0f1f2", "light": "#1e1e21"}
MUTED = {"dark": "#9ca4af", "light": "#5c5c5c"}
GROUND = {"dark": "#1b1c21", "light": "#f7f7f7"}
_theme = "dark"


def use(theme: str = "dark") -> None:
    global _theme
    _theme = theme
    plt.style.use(HERE / f"napari-{theme}.mplstyle")


def theme() -> str:
    return _theme


def save(fig, stem: str | Path) -> None:
    stem = Path(stem)
    stem.parent.mkdir(parents=True, exist_ok=True)
    for ext in ("svg", "png"):
        fig.savefig(f"{stem}-{_theme}.{ext}", bbox_inches="tight")
