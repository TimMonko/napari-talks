"""Regenerate napari screenshots for slides, in both themes, from code.

    pixi run -e demos screenshots                 # writes shared/media/generated/*.png

Every screenshot in a talk should come from a script like this, so it can be
redone when napari's UI changes instead of re-cropping by hand.
"""
from pathlib import Path

import imageio.v3 as iio
import napari
import numpy as np
from skimage import data

OUT = Path(__file__).parents[1] / "media" / "generated"
OUT.mkdir(parents=True, exist_ok=True)


def build_viewer(theme: str) -> napari.Viewer:
    viewer = napari.Viewer(show=False)
    viewer.theme = theme
    cells = data.cells3d()
    viewer.add_image(cells[:, 1], name="nuclei", colormap="magma", blending="additive")
    viewer.add_image(cells[:, 0], name="membranes", colormap="green", blending="additive", opacity=0.6)
    pts = np.array([[30, 120, 128], [30, 80, 170], [30, 170, 60], [30, 205, 150], [30, 45, 75]])
    viewer.add_points(pts, name="annotations", size=12, face_color="#ccb98f")
    viewer.add_labels((cells[:, 1] > 9000).astype(int), name="nuclei mask", opacity=0.4, visible=False)
    viewer.layers.selection.active = viewer.layers["annotations"]
    viewer.dims.current_step = (30, 0, 0)
    viewer.window.resize(1400, 820)
    return viewer


def grab(widget, path: Path) -> None:
    widget.grab().save(str(path))


for theme in ("dark", "light"):
    viewer = build_viewer(theme)
    iio.imwrite(OUT / f"viewer-{theme}.png", viewer.screenshot(canvas_only=False, flash=False))
    # the layer list dock on its own (private Qt attribute; fine for a slide script)
    grab(viewer.window._qt_viewer.dockLayerList, OUT / f"layer-list-{theme}.png")
    viewer.close()
print("wrote", sorted(p.name for p in OUT.iterdir()))
