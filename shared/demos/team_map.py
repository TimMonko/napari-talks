"""
The napari team on a world map
===============================

Show where the napari team is, with nothing but 2D layers: a satellite
image of the Earth as an Image, country borders as a Shapes layer of paths,
one RGBA Image *mosaic* of square avatars per team group, each with a
coloured frame per role drawn into its pixels, and an invisible Points
layer of one square per person whose features show in the status bar when
you hover over a person. Every layer declares its units (degrees) and axis
labels (latitude, longitude), which label the status bar and the
coordinate grid. The grid and the tooltip are set up but off; turn them on
from the View menu or by setting ``visible = True``.

All layers share one world coordinate system, (latitude, longitude) in
degrees, set through ``scale`` and ``translate`` as in the xarray lat/lon
example: the satellite image is an equirectangular grid, so it needs no
reprojection. Latitude increases upwards, so the camera's vertical axis is
flipped to point up.

People who live close together are scattered around their city like
photos on a table: tiles that sit too close push each other apart along
the real direction between them, while a weak spring keeps each near
home, so neighbours overlap a little and keep their relative positions.

    pixi run -e demos team-map   # also writes shared/media/generated/team-map.png

Before showing the viewer, ``export_figure`` saves the map for slides. It
crops to the extent of the layers (the whole globe, so the theme doesn't
show) and renders at the finest layer's resolution (the Blue Marble image,
15 px per degree), so 5400 x 2700.

Map: NASA Blue Marble Next Generation (July 2004), public domain. Borders:
Natural Earth 1:110m admin-0 countries, public domain.

.. tags:: visualization-advanced, layers, features-table
"""

import json
import tomllib
from collections import defaultdict
from pathlib import Path

import imageio.v3 as iio
import numpy as np
import pooch
from skimage.transform import resize

import napari
from napari.utils.colormaps.standardize_color import transform_color

DATA = Path(__file__).parents[1] / 'data'
TEAM = DATA / 'team.toml'
FIGURES = Path(__file__).parents[1] / 'media' / 'generated'

DIM = 0.8  # darken the satellite image so avatars stand out
AVATAR = 11.0  # avatar side, in degrees
AVATAR_PX = 128  # avatar resolution in the mosaics
FRAME_PX = 7  # frame stroke width in avatar pixels; frames sit inside the tile
SPACING = 0.85  # closest two tiles may sit, as a fraction of AVATAR; below 1 overlaps

# every layer lives in the same geographic world coordinates
GEO = {'units': ('degree', 'degree'), 'axis_labels': ('latitude', 'longitude')}

ROLE_LABELS = {'sc': 'steering council', 'core': 'core team', 'triage': 'triage'}
FRAME_COLORS = {
    'sc': '#d4a72c',
    'core': '#009bf2',
    'triage': '#1f9e80',
    'emeritus': '#8a8f98',
}
# frames from the outside in
FRAME_ORDER = ['sc', 'core', 'triage']
# one mosaic layer per group; each person is in their most senior current role
GROUPS = ['emeritus', 'triage', 'core', 'sc']
GROUP_NAMES = {
    'sc': 'steering council',
    'core': 'core team',
    'triage': 'triage team',
    'emeritus': 'emeritus',
}


# --- data -----------------------------------------------------------------


def load_team(path: Path) -> list[dict]:
    people = tomllib.loads(path.read_text(encoding='utf-8'))['person']
    people = [p for p in people if 'lat' in p]
    for p in people:
        p['group'] = next((r for r in FRAME_ORDER if r in p['roles']), 'emeritus')
        p['frames'] = [r for r in FRAME_ORDER if r in p['roles']] or ['emeritus']
        photo = p.get('photo') or f'avatars/{p["handle"]}.png'
        p['photo_path'] = DATA / photo
    return people


def blue_marble() -> np.ndarray:
    """NASA Blue Marble: an equirectangular image, rows north to south."""
    path = pooch.retrieve(
        'https://assets.science.nasa.gov/content/dam/science/esd/eo/images/'
        'bmng/bmng-base/july/world.200407.3x5400x2700.jpg',
        known_hash='f55226d46d27e05511f2118dc6aa24f5dbf9b6b2cddc87cc6e0e7dd067c00b11',
    )
    return iio.imread(path)


def countries() -> list[np.ndarray]:
    """Outer rings of every country, as (N, 2) arrays of (lat, lon)."""
    path = pooch.retrieve(
        'https://raw.githubusercontent.com/nvkelso/natural-earth-vector/'
        'v5.1.2/geojson/ne_110m_admin_0_countries.geojson',
        known_hash='6866c877d39cba9c357620878839b336d569f8c662d3cfab4cb1dbe2d39c977f',
    )
    rings = []
    for feature in json.loads(Path(path).read_text(encoding='utf-8'))['features']:
        geom = feature['geometry']
        polys = geom['coordinates']
        if geom['type'] == 'Polygon':
            polys = [polys]
        rings += [np.asarray(poly[0])[:, ::-1] for poly in polys]
    return rings


def avatar_tile(path: Path, roles: list[str], size: int) -> np.ndarray:
    """An RGBA avatar, centre-cropped to a square, with one frame per role.

    Frames are drawn into the pixels, most senior role outermost, so a tile
    that overlaps another covers its frame too.
    """
    img = iio.imread(path)
    if img.ndim == 2:
        img = np.stack([img] * 3, axis=-1)
    h, w = img.shape[:2]
    s = min(h, w)
    img = img[(h - s) // 2 : (h + s) // 2, (w - s) // 2 : (w + s) // 2, :3]
    img = resize(img, (size, size), anti_aliasing=True, preserve_range=True)
    # distance of each pixel from the tile edge, in pixels
    i = np.arange(size)
    edge = np.minimum.outer(np.minimum(i, i[::-1]), np.minimum(i, i[::-1]))
    for k, role in enumerate(roles):
        band = (edge >= k * FRAME_PX) & (edge < (k + 1) * FRAME_PX)
        img[band] = transform_color(FRAME_COLORS[role])[0, :3] * 255
    rgba = np.dstack([img, np.full((size, size), 255)]).astype(np.uint8)
    return rgba[::-1]  # rows run south to north on the map


# --- layout ---------------------------------------------------------------


def layout(anchors: np.ndarray, spacing: float, iterations: int = 400) -> np.ndarray:
    """Move tiles off each other while keeping them near home.

    Everyone starts at their real location; people who share a city start
    on a small sunflower spiral around it, so they have a direction to move
    in. Each round, any two tiles closer than ``spacing`` (as squares: the
    larger of the two axis distances) push each other apart along the line
    between them, and a weak spring, fading to nothing by the last round,
    pulls each tile back towards home. The
    push follows the real direction between people, so someone south-east
    of a neighbour stays south-east of them.
    """
    pos = anchors.astype(float).copy()
    golden = np.pi * (3 - np.sqrt(5))
    _, city, counts = np.unique(anchors, axis=0, return_inverse=True, return_counts=True)
    for c in np.flatnonzero(counts > 1):
        k = np.arange(counts[c])
        offsets = np.sqrt(k + 0.5)[:, None] * np.c_[np.sin(k * golden), np.cos(k * golden)]
        pos[city.ravel() == c] += 0.3 * spacing * offsets

    # the spring fades out, so the last rounds only separate tiles
    for pull in np.linspace(0.02, 0, iterations):
        delta = pos[:, None] - pos[None]
        dist = np.hypot(delta[..., 0], delta[..., 1])
        np.fill_diagonal(dist, np.inf)
        overlap = np.clip(spacing - np.abs(delta).max(axis=-1), 0, None)
        np.fill_diagonal(overlap, 0)
        push = (delta / dist[..., None] * overlap[..., None] / 2).sum(axis=1)
        pos += push + pull * (anchors - pos)
    return pos


# --- layers ---------------------------------------------------------------


def mosaic(people: list[dict]) -> tuple[np.ndarray, dict]:
    """Paste every avatar into one RGBA image; return it with its transform.

    ``people`` is in drawing order: later tiles cover earlier ones.
    """
    px = AVATAR / AVATAR_PX
    xy = np.array([p['xy'] for p in people])
    origin = xy.min(axis=0) - AVATAR / 2
    extent = np.ceil((xy.max(axis=0) - origin + AVATAR / 2) / px).astype(int) + 1
    canvas = np.zeros((*extent, 4), dtype=np.uint8)
    for p in people:
        r, c = np.round((p['xy'] - AVATAR / 2 - origin) / px).astype(int)
        tile = avatar_tile(p['photo_path'], p['frames'], AVATAR_PX)
        canvas[r : r + AVATAR_PX, c : c + AVATAR_PX] = tile
    return canvas, {'scale': (px, px), 'translate': tuple(origin)}


def person_features(people: list[dict]) -> dict:
    """What to show when hovering over a person."""
    features = defaultdict(list)
    for p in people:
        features['name'].append(p['name'])
        features['handle'].append('@' + p['handle'])
        roles = [ROLE_LABELS[r] for r in p['roles']]
        roles += [f'emeritus {ROLE_LABELS[r]}' for r in p['emeritus']]
        features['roles'].append(', '.join(roles))
        features['place'].append(p['place'])
    return dict(features)


def main() -> napari.Viewer:
    people = load_team(TEAM)
    anchors = np.array([[p['lat'], p['lon']] for p in people])
    for p, xy in zip(people, layout(anchors, AVATAR * SPACING), strict=True):
        p['xy'] = xy
    # drawing order: by group as the layers stack, then north to south, so
    # within a cluster each row slightly covers the row above it
    people.sort(key=lambda p: (GROUPS.index(p['group']), -p['xy'][0]))

    viewer = napari.Viewer(title='napari team')
    earth = blue_marble()
    deg_per_px = 180 / earth.shape[0]
    viewer.add_image(
        (earth[::-1] * DIM).astype(np.uint8),  # rows south to north
        rgb=True,
        name='Blue Marble',
        scale=(deg_per_px, deg_per_px),
        translate=(-90 + deg_per_px / 2, -180 + deg_per_px / 2),
        interpolation2d='linear',
        **GEO,
    )
    viewer.add_shapes(
        countries(),
        shape_type='path',
        name='countries',
        edge_color='white',
        edge_width=0.1,
        opacity=0.3,
        **GEO,
    )
    for group in GROUPS:
        members = [p for p in people if p['group'] == group]
        if not members:
            continue
        data, transform = mosaic(members)
        viewer.add_image(
            data,
            rgb=True,
            name=GROUP_NAMES[group],
            opacity=0.55 if group == 'emeritus' else 1.0,
            interpolation2d='linear',
            **transform,
            **GEO,
        )

    # one invisible square point per person, the size of a tile and in
    # drawing order, so the tile on top wins the hover. Points show their
    # features in the status bar (this layer is selected, being added last)
    # and, when the tooltip is on, next to the cursor.
    viewer.add_points(
        np.array([p['xy'] for p in people]),
        name='people',
        symbol='square',
        size=AVATAR,
        features=person_features(people),
        face_color='transparent',
        border_width=0,
        **GEO,
    )
    # Overlays are set up but off; set `visible = True` to show them (also
    # from the View menu). The tooltip shows the hovered person's features.
    viewer.tooltip.visible = False

    # A latitude/longitude graticule with tick labels. Its axis labels
    # ("latitude (deg)", from the layers' axis_labels and units) are off:
    # in napari 0.10.0 the vertical one overlaps the tick labels, and on a
    # world map the ticks speak for themselves. export_figure crops to the
    # data, which cuts off the tick labels, so keep it off for exports.
    grid = viewer.scene.overlays['coordinate_grid']
    grid.n_ticks = 7
    grid.axis_labels = False
    grid.opacity = 0.5
    grid.visible = False

    viewer.scene.camera.orientation2d = ('up', 'right')
    viewer.reset_view()  # the default margin leaves room for the grid labels
    return viewer


if __name__ == '__main__':
    viewer = main()
    FIGURES.mkdir(parents=True, exist_ok=True)
    viewer.export_figure(str(FIGURES / 'team-map.png'))
    napari.run()
