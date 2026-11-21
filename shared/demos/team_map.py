"""
The napari team on a world map
===============================

Show where the napari team is, with nothing but 2D layers: a satellite
image of the Earth as an Image, country borders as a Shapes layer of paths,
one RGBA Image *mosaic* of square avatars per team group, and a Shapes
layer of square frames (one frame per role) whose features show in the
status bar when you hover over a person with that layer selected.

Everything is drawn in the Equal Earth projection (Šavrič, Patterson and
Jenny, 2018), an equal-area world projection with closed-form equations.
Locations and country borders go through the forward projection; the
satellite image, which comes as a plain latitude/longitude grid, is
resampled through the inverse. Map units are degrees at the equator.
North is up, so the camera's vertical axis is flipped.

People who live close together would overlap, so each cluster is packed
on a square grid around its centre, followed by a few rounds of pushing
apart clusters that still collide.

    pixi run -e demos team-map                      # shared/data/team.toml
    pixi run -e demos team-map path/to/team.toml    # another roster

Map: NASA Blue Marble Next Generation (July 2004), public domain. Borders:
Natural Earth 1:110m admin-0 countries, public domain.

.. tags:: visualization-advanced, layers, features-table
"""

import json
import sys
import tomllib
from collections import defaultdict
from pathlib import Path

import imageio.v3 as iio
import numpy as np
import pooch
from skimage.transform import resize

import napari

DATA = Path(__file__).parents[1] / 'data'
TEAM = Path(sys.argv[1]) if len(sys.argv) > 1 else DATA / 'team.toml'

PAD = 12  # map margin around the people, in map units
UNITS_PER_PX = 0.06  # resolution of the projected map
DIM = 0.8  # darken the satellite image so avatars stand out

AVATAR = 7.0  # avatar side, in map units
AVATAR_PX = 128  # avatar resolution in the mosaics
FRAME = 0.4  # frame stroke width; frames sit inside the tile
GAP = 0.2  # space between neighbouring tiles

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
    for p in people:
        p['group'] = next((r for r in FRAME_ORDER if r in p['roles']), 'emeritus')
        p['frames'] = [r for r in FRAME_ORDER if r in p['roles']] or ['emeritus']
        photo = p.get('photo') or f'avatars/{p["handle"]}.png'
        p['photo_path'] = DATA / photo
    return people


def blue_marble() -> np.ndarray:
    """NASA Blue Marble, equirectangular: rows run north to south."""
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


# --- Equal Earth projection -------------------------------------------------

A1, A2, A3, A4 = 1.340264, -0.081106, 0.000893, 0.003796
M = np.sqrt(3) / 2


def _y(t):
    return t * (A1 + A2 * t**2 + A3 * t**6 + A4 * t**8)


def _dy(t):
    return A1 + 3 * A2 * t**2 + 7 * A3 * t**6 + 9 * A4 * t**8


def project(latlon: np.ndarray) -> np.ndarray:
    """(lat, lon) in degrees -> (y, x) in map units."""
    lat, lon = np.deg2rad(latlon).T
    t = np.arcsin(M * np.sin(lat))
    x = lon * np.cos(t) / (M * _dy(t))
    return np.rad2deg(np.stack([_y(t), x], axis=-1))


def unproject(yx: np.ndarray) -> np.ndarray:
    """(y, x) in map units -> (lat, lon) in degrees; NaN off the globe."""
    y, x = np.deg2rad(yx).T
    t = y.copy()
    for _ in range(10):  # Newton's method on y = _y(t)
        t -= (_y(t) - y) / _dy(t)
    with np.errstate(invalid='ignore'):
        lat = np.arcsin(np.sin(t) / M)
        lon = x * M * _dy(t) / np.cos(t)
    lon[np.abs(lon) > np.pi] = np.nan
    return np.rad2deg(np.stack([lat, lon], axis=-1))


def projected_map(image: np.ndarray, box: np.ndarray) -> np.ndarray:
    """Resample the equirectangular image into ``box`` ((y0, x0), (y1, x1)).

    Output rows run south to north; pixels off the globe are transparent.
    """
    ys = np.arange(box[0, 0], box[1, 0], UNITS_PER_PX) + UNITS_PER_PX / 2
    xs = np.arange(box[0, 1], box[1, 1], UNITS_PER_PX) + UNITS_PER_PX / 2
    grid = np.stack(np.meshgrid(ys, xs, indexing='ij'), axis=-1).reshape(-1, 2)
    lat, lon = unproject(grid).T
    off = np.isnan(lon)
    h, w = image.shape[:2]
    rows = ((90 - lat) / 180 * h).astype(int).clip(0, h - 1)
    cols = ((np.nan_to_num(lon) + 180) / 360 * w).astype(int).clip(0, w - 1)
    rgb = image[rows, cols, :3] * DIM
    alpha = np.where(off, 0, 255)
    return np.column_stack([rgb, alpha]).astype(np.uint8).reshape(len(ys), len(xs), 4)


def clip_paths(rings: list[np.ndarray], box: np.ndarray) -> list[np.ndarray]:
    """Split each ring into the runs of vertices that fall inside ``box``."""
    paths = []
    for ring in rings:
        inside = np.all((ring >= box[0]) & (ring <= box[1]), axis=1)
        breaks = np.flatnonzero(np.diff(inside.astype(int))) + 1
        for run in np.split(np.arange(len(ring)), breaks):
            if inside[run[0]] and len(run) > 1:
                paths.append(ring[run])
    return paths


# --- avatars and layout ---------------------------------------------------


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


def square_spiral(n: int) -> np.ndarray:
    """n offsets on a unit square grid, nearest the centre first."""
    k = int(np.ceil(np.sqrt(n)))
    ij = np.stack(np.mgrid[-k : k + 1, -k : k + 1], axis=-1).reshape(-1, 2)
    order = np.lexsort((np.arctan2(ij[:, 0], ij[:, 1]), np.hypot(ij[:, 0], ij[:, 1])))
    return ij[order[:n]].astype(float)


def layout(anchors: np.ndarray, spacing: float, iterations: int = 100) -> np.ndarray:
    """Spread square tiles so no two overlap.

    People within ``spacing`` of a cluster's first member join that cluster;
    each cluster is packed on a square grid around its mean position. Tiles
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
        pos[c] = anchors[c].mean(axis=0) + square_spiral(len(c)) * spacing

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
            features['place'].append(p.get('place') or 'not shared')
    # draw big frames first so smaller ones sit on top and win the hover
    order = np.argsort([s[0, 0] - s[1, 0] for s in shapes], kind='stable')
    return (
        [shapes[i] for i in order],
        {k: [v[i] for i in order] for k, v in features.items()},
        [colors[i] for i in order],
    )


def main() -> napari.Viewer:
    people = [p for p in load_team(TEAM) if 'lat' in p]
    anchors = project(np.array([[p['lat'], p['lon']] for p in people]))
    spacing = AVATAR + GAP
    for p, xy in zip(people, layout(anchors, spacing), strict=True):
        p['xy'] = xy

    # crop the map to the people, plus a margin
    xy = np.array([p['xy'] for p in people])
    box = np.array([xy.min(axis=0) - spacing / 2 - PAD, xy.max(axis=0) + spacing / 2 + PAD])

    viewer = napari.Viewer(title='napari team')
    viewer.add_image(
        projected_map(blue_marble(), box),
        rgb=True,
        name='Blue Marble',
        scale=(UNITS_PER_PX, UNITS_PER_PX),
        translate=tuple(box[0]),
        interpolation2d='linear',
    )
    viewer.add_shapes(
        clip_paths([project(r) for r in countries()], box),
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
    napari.run()
