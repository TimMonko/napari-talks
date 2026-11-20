"""Screenshot the team map for slides, in both napari themes.

    pixi run -e demos team-map-screenshots   # shared/media/generated/team-map-{dark,light}.png

Opens a real window (napari needs a display to size the canvas).
"""

import runpy
from pathlib import Path

import imageio.v3 as iio
from qtpy.QtWidgets import QApplication

OUT = Path(__file__).parents[1] / "media" / "generated"
OUT.mkdir(parents=True, exist_ok=True)

team_map = runpy.run_path(str(Path(__file__).with_name("team_map.py")), run_name="team_map")
viewer = team_map["main"]()
viewer.window.resize(1920, 1080)
QApplication.processEvents()
viewer.reset_view(margin=0)
for theme in ("dark", "light"):
    viewer.theme = theme
    iio.imwrite(OUT / f"team-map-{theme}.png", viewer.screenshot(canvas_only=True, flash=False))
viewer.close()
print("wrote", [p.name for p in sorted(OUT.glob("team-map-*.png"))])
