"""
The napari team on a world map
===============================

Show where the napari team is, with nothing but 2D layers: a satellite
image of the Earth as an Image, country borders as a Shapes layer of paths,
one RGBA Image *mosaic* of square avatars per team group, and a Shapes
layer of square frames (one frame per role) whose features show in the
status bar when you hover over a person with that layer selected.

All layers share one world coordinate system, (latitude, longitude) in
degrees, set through ``scale`` and ``translate`` as in the xarray lat/lon
example: the satellite image is an equirectangular grid, so it needs no
reprojection. Latitude increases upwards, so the camera's vertical axis is
flipped to point up.

People who live close together would overlap, so each cluster is packed
on a square grid around its centre, each person in the free slot nearest
their real location, followed by a few rounds of pushing apart clusters
that still collide.

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
from scipy.optimize import linear_sum_assignment
from skimage.transform import resize

import napari

DATA = Path(__file__).parents[1] / 'data'
TEAM = DATA / 'team.toml'
FIGURES = Path(__file__).parents[1] / 'media' / 'generated'

DIM = 0.8  # darken the satellite image so avatars stand out
AVATAR = 12.0  # avatar side, in degrees
AVATAR_PX = 128  # avatar resolution in the mosaics
FRAME = 0.7  # frame stroke width; frames sit inside the tile
GAP = 0.4  # space between neighbouring tiles

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


def square_avatar(path: Path, size: int) -> np.ndarray:
    """An RGBA avatar, centre-cropped to a square."""
    img = iio.imread(path)
    if img.ndim == 2:
        img = np.stack([img] * 3, axis=-1)
    h, w = img.shape[:2]
    s = min(h, w)
    img = img[(h - s) // 2 : (h + s) // 2, (w - s) // 2 : (w + s) // 2, :3]
    img = resize(img, (size, size), anti_aliasing=True, preserve_range=True)
    rgba = np.dstack([img, np.full((size, size), 255)]).astype(np.uint8)
    return rgba[::-1]  # rows run south to north on the map


# --- layout ---------------------------------------------------------------


def square_spiral(n: int) -> np.ndarray:
    """n offsets on a unit square grid, nearest the centre first."""
    k = int(np.ceil(np.sqrt(n)))
    ij = np.stack(np.mgrid[-k : k + 1, -k : k + 1], axis=-1).reshape(-1, 2)
    order = np.lexsort((np.arctan2(ij[:, 0], ij[:, 1]), np.hypot(ij[:, 0], ij[:, 1])))
    return ij[order[:n]].astype(float)


def layout(anchors: np.ndarray, spacing: float, iterations: int = 100) -> np.ndarray:
    """Spread square tiles so no two overlap.

    People within ``spacing`` of a cluster's first member join that cluster.
    Each cluster gets a compact block of grid slots around its mean
    position, and each person takes the slot nearest their real location
    (an optimal assignment), so north stays north inside a block. Tiles
    that still overlap are pushed apart along the axis where they are
    furthest apart, which keeps them in tidy rows and columns.
    """
    clusters: list[list[int]] = []
    for i, a in enumerate(anchors):
        for c in clusters:
            if np.abs(anchors[c[0]] - a).max() < spacing:
                c.append(i)
                break
        else:
            clusters.append([i])

    pos = anchors.astype(float).copy()
    for c in clusters:
        slots = anchors[c].mean(axis=0) + square_spiral(len(c)) * spacing
        cost = ((anchors[c][:, None] - slots[None]) ** 2).sum(axis=-1)
        people, chosen = linear_sum_assignment(cost)
        pos[np.asarray(c)[people]] = slots[chosen]

    for _ in range(iterations):
        delta = pos[:, None] - pos[None]
        gap = np.abs(delta).max(axis=-1)
        np.fill_diagonal(gap, np.inf)
        overlap = np.clip(spacing - gap, 0, None)
        if not overlap.any():
            break
        axis = np.abs(delta).argmax(axis=-1)
        direction = np.sign(delta) * (np.arange(2) == axis[..., None])
        pos += (direction * overlap[..., None] / 2).sum(axis=1)
    return pos


# --- layers ---------------------------------------------------------------


def mosaic(people: list[dict]) -> tuple[np.ndarray, dict]:
    """Paste every avatar into one RGBA image; return it with its transform."""
    px = AVATAR / AVATAR_PX
    xy = np.array([p['xy'] for p in people])
    origin = xy.min(axis=0) - AVATAR / 2
    extent = np.ceil((xy.max(axis=0) - origin + AVATAR / 2) / px).astype(int) + 1
    canvas = np.zeros((*extent, 4), dtype=np.uint8)
    for p in people:
        r, c = np.round((p['xy'] - AVATAR / 2 - origin) / px).astype(int)
        canvas[r : r + AVATAR_PX, c : c + AVATAR_PX] = square_avatar(p['photo_path'], AVATAR_PX)
    return canvas, {'scale': (px, px), 'translate': tuple(origin)}


def frames(people: list[dict]) -> tuple[list[np.ndarray], dict, list[str]]:
    """Concentric square frames (two corners), largest first, with features."""
    shapes, colors = [], []
    features = defaultdict(list)
    for p in people:
        for k, role in enumerate(p['frames']):
            # frames are inset from the tile edge, most senior role outermost
            half = AVATAR / 2 - FRAME * (k + 0.5)
            shapes.append(np.array([p['xy'] - half, p['xy'] + half]))
            colors.append(FRAME_COLORS[role])
            features['name'].append(p['name'])
            features['handle'].append('@' + p['handle'])
            roles = [ROLE_LABELS[r] for r in p['roles']]
            roles += [f'emeritus {ROLE_LABELS[r]}' for r in p['emeritus']]
            features['roles'].append(', '.join(roles))
            features['place'].append(p['place'])
    # draw big frames first so smaller ones sit on top and win the hover
    order = np.argsort([s[0, 0] - s[1, 0] for s in shapes], kind='stable')
    return (
        [shapes[i] for i in order],
        {k: [v[i] for i in order] for k, v in features.items()},
        [colors[i] for i in order],
    )


def main() -> napari.Viewer:
    people = load_team(TEAM)
    anchors = np.array([[p['lat'], p['lon']] for p in people])
    for p, xy in zip(people, layout(anchors, AVATAR + GAP), strict=True):
        p['xy'] = xy

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
    )
    viewer.add_shapes(
        countries(),
        shape_type='path',
        name='countries',
        edge_color='white',
        edge_width=0.1,
        opacity=0.3,
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
            opacity=0.45 if group == 'emeritus' else 1.0,
            interpolation2d='linear',
            **transform,
        )

    shapes, features, colors = frames(people)
    viewer.add_shapes(
        shapes,
        shape_type='rectangle',
        name='roles',
        features=features,
        face_color='transparent',
        edge_color=colors,
        edge_width=FRAME,
    )
    # the last layer added is selected, so hovering shows its features

    viewer.scene.camera.orientation2d = ('up', 'right')
    viewer.reset_view(margin=0)
    return viewer


if __name__ == '__main__':
    viewer = main()
    FIGURES.mkdir(parents=True, exist_ok=True)
    viewer.export_figure(str(FIGURES / 'team-map.png'))
    napari.run()
