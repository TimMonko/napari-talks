"""
The napari team on a world map
===============================

Show where the napari team is, with nothing but 2D layers: a muted world map
as an Image, one RGBA Image *mosaic* of avatars per team group, and a Shapes
layer of concentric rings (one ring per role) whose features appear in the
tooltip when you hover over a person.

All layers share one world coordinate system, (latitude, longitude) in
degrees, set through ``scale`` and ``translate`` as in the xarray lat/lon
example. Latitude increases upwards, so the camera's vertical axis is
flipped to point up.

People who live close together would overlap, so each cluster is laid out
on a hexagonal spiral around its centre, followed by a few rounds of
pairwise repulsion between clusters.

    pixi run -e demos team-map                      # shared/data/team.toml
    pixi run -e demos team-map path/to/team.toml    # another roster

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
from skimage.draw import polygon as fill_polygon
from skimage.transform import resize

import napari
from napari.utils.colormaps import Colormap

DATA = Path(__file__).parents[1] / 'data'
TEAM = Path(sys.argv[1]) if len(sys.argv) > 1 else DATA / 'team.toml'

LAT_RANGE = (-60, 80)  # crop Antarctica and the high Arctic
DEG_PER_PX = 0.1  # world map resolution
AVATAR_DEG = 8.0  # avatar diameter, in degrees
AVATAR_PX = 128  # avatar resolution in the mosaics
RING_DEG = 0.8  # ring stroke width
RING_GAP = 0.3  # space between the avatar and its first ring
SPREAD = 1.08  # spacing between neighbours, as a multiple of the widest person
UNPLACED_LAT = -68  # row under the map for people without a location

ROLE_LABELS = {'sc': 'steering council', 'core': 'core team', 'triage': 'triage'}
RING_COLORS = {
    'sc': '#d4a72c',
    'core': '#009bf2',
    'triage': '#1f9e80',
    'emeritus': '#8a8f98',
}
# rings from the outside in
RING_ORDER = ['sc', 'core', 'triage']
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
        p['group'] = next((r for r in RING_ORDER if r in p['roles']), 'emeritus')
        p['rings'] = [r for r in RING_ORDER if r in p['roles']] or ['emeritus']
        photo = p.get('photo') or f'avatars/{p["handle"]}.png'
        p['photo_path'] = DATA / photo
    return people


def world_land() -> np.ndarray:
    """Rasterize Natural Earth 1:110m land onto a (lat, lon) grid."""
    path = pooch.retrieve(
        'https://raw.githubusercontent.com/nvkelso/natural-earth-vector/'
        'v5.1.2/geojson/ne_110m_land.geojson',
        known_hash='9e0729ee253ca7d7a5c4ae9395fb1902264c5377c52e224d13dd85010e2835d9',
    )
    shape = (
        round((LAT_RANGE[1] - LAT_RANGE[0]) / DEG_PER_PX),
        round(360 / DEG_PER_PX),
    )
    land = np.zeros(shape, dtype=np.uint8)
    for feature in json.loads(Path(path).read_text())['features']:
        geom = feature['geometry']
        polys = geom['coordinates']
        if geom['type'] == 'Polygon':
            polys = [polys]
        for poly in polys:
            lon, lat = np.asarray(poly[0]).T  # outer ring; 110m land has no holes
            rows = (lat - LAT_RANGE[0]) / DEG_PER_PX
            cols = (lon + 180) / DEG_PER_PX
            land[fill_polygon(rows, cols, shape)] = 1
    return land


def round_avatar(path: Path, size: int) -> np.ndarray:
    """An RGBA avatar cut to an anti-aliased disc."""
    img = iio.imread(path)
    if img.ndim == 2:
        img = np.stack([img] * 3, axis=-1)
    img = resize(img[..., :3], (size, size), anti_aliasing=True, preserve_range=True)
    yy, xx = np.mgrid[:size, :size] + 0.5 - size / 2
    alpha = np.clip(size / 2 - np.hypot(yy, xx), 0, 1)
    rgba = np.dstack([img, alpha * 255]).astype(np.uint8)
    return rgba[::-1]  # rows run south to north on the map


# --- layout ---------------------------------------------------------------


def hex_spiral(n: int) -> np.ndarray:
    """n offsets on a hexagonal lattice with unit spacing, centre first."""
    angles = np.deg2rad(np.arange(0, 360, 60))
    dirs = np.stack([np.cos(angles), np.sin(angles)], axis=1)
    out = [np.zeros(2)]
    ring = 1
    while len(out) < n:
        pos = ring * dirs[4]  # walk the ring's six sides from one corner
        for d in dirs:
            for _ in range(ring):
                out.append(pos.copy())
                pos = pos + d
        ring += 1
    return np.array(out[:n])


def layout(anchors: np.ndarray, spacing: float, iterations: int = 60) -> np.ndarray:
    """Spread people so no two are closer than ``spacing``.

    People within ``spacing`` of a cluster's first member join that cluster;
    each cluster goes on a hex spiral around its mean position. Repulsion then
    separates clusters that still collide.
    """
    clusters: list[list[int]] = []
    for i, a in enumerate(anchors):
        for c in clusters:
            if np.hypot(*(anchors[c[0]] - a)) < spacing:
                c.append(i)
                break
        else:
            clusters.append([i])

    pos = anchors.astype(float).copy()
    for c in clusters:
        centre = anchors[c].mean(axis=0)
        pos[c] = centre + hex_spiral(len(c)) * spacing

    for _ in range(iterations):
        delta = pos[:, None] - pos[None]
        dist = np.hypot(delta[..., 0], delta[..., 1])
        np.fill_diagonal(dist, np.inf)
        overlap = np.clip(spacing - dist, 0, None)
        if not overlap.any():
            break
        push = (delta / dist[..., None] * overlap[..., None] / 2).sum(axis=1)
        pos += push
    return pos


def place_people(people: list[dict], spacing: float) -> None:
    placed = [p for p in people if 'lat' in p]
    if placed:
        anchors = np.array([[p['lat'], p['lon']] for p in placed])
        for p, xy in zip(placed, layout(anchors, spacing), strict=True):
            p['xy'] = xy
    unplaced = [p for p in people if 'lat' not in p]
    lons = (np.arange(len(unplaced)) - (len(unplaced) - 1) / 2) * spacing
    for p, lon in zip(unplaced, lons, strict=True):
        p['xy'] = np.array([UNPLACED_LAT, lon])


# --- layers ---------------------------------------------------------------


def mosaic(people: list[dict]) -> tuple[np.ndarray, dict]:
    """Paste every avatar into one RGBA image; return it with its transform."""
    px = AVATAR_DEG / AVATAR_PX
    xy = np.array([p['xy'] for p in people])
    origin = xy.min(axis=0) - AVATAR_DEG / 2
    extent = np.ceil((xy.max(axis=0) - origin + AVATAR_DEG / 2) / px).astype(int)
    canvas = np.zeros((*extent, 4), dtype=np.uint8)
    for p in people:
        r, c = np.round((p['xy'] - AVATAR_DEG / 2 - origin) / px).astype(int)
        tile = round_avatar(p['photo_path'], AVATAR_PX)
        view = canvas[r : r + AVATAR_PX, c : c + AVATAR_PX]
        view[:] = np.where(tile[..., 3:] > view[..., 3:], tile, view)
    return canvas, {'scale': (px, px), 'translate': tuple(origin)}


def rings(people: list[dict]) -> tuple[list[np.ndarray], dict, list[str]]:
    """Concentric ellipses (centre, radii), largest first, with features."""
    shapes, colors = [], []
    features = defaultdict(list)
    for p in people:
        for k, role in enumerate(reversed(p['rings'])):
            # innermost ring first in the loop, so it gets the smallest radius
            r = AVATAR_DEG / 2 + RING_GAP + RING_DEG * (k + 0.5)
            shapes.append(np.array([p['xy'], [r, r]]))
            colors.append(RING_COLORS[role])
            features['name'].append(p['name'])
            features['handle'].append('@' + p['handle'])
            roles = [ROLE_LABELS[r] for r in p['roles']]
            roles += [f'emeritus {ROLE_LABELS[r]}' for r in p['emeritus']]
            features['roles'].append(', '.join(roles))
            features['place'].append(p.get('place') or 'not shared')
    # draw big rings first so smaller ones sit on top and win the hover
    order = np.argsort([-s[1, 0] for s in shapes], kind='stable')
    return (
        [shapes[i] for i in order],
        {k: [v[i] for i in order] for k, v in features.items()},
        [colors[i] for i in order],
    )


def main() -> napari.Viewer:
    people = load_team(TEAM)
    widest = AVATAR_DEG + 2 * (RING_GAP + RING_DEG * max(len(p['rings']) for p in people))
    place_people(people, widest * SPREAD)

    viewer = napari.Viewer(title='napari team')
    land = world_land()
    viewer.add_image(
        land,
        name='world',
        scale=(DEG_PER_PX, DEG_PER_PX),
        translate=(LAT_RANGE[0], -180),
        colormap=Colormap([[0, 0, 0, 0], [0.5, 0.5, 0.52, 1]], name='land'),
        contrast_limits=(0, 1),
        opacity=0.45,
        interpolation2d='linear',
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
            opacity=0.35 if group == 'emeritus' else 1.0,
            interpolation2d='linear',
            **transform,
        )

    shapes, features, colors = rings(people)
    ring_layer = viewer.add_shapes(
        shapes,
        shape_type='ellipse',
        name='roles',
        features=features,
        face_color='transparent',
        edge_color=colors,
        edge_width=RING_DEG,
        opacity=0.95,
    )

    # Shapes layers don't put their features in the tooltip yet, so do it
    # here; connect last so this runs after the viewer's own update.
    viewer.tooltip.visible = True

    def show_person(event=None):
        index, _ = ring_layer.get_value(viewer.cursor.position, world=True)
        if index is None:
            return
        row = ring_layer.features.iloc[index]
        viewer.tooltip.text = f'{row["name"]} ({row.handle})\n{row.roles}\n{row.place}'

    viewer.cursor.events.position.connect(show_person, position='last')

    viewer.scene.camera.orientation2d = ('up', 'right')
    viewer.reset_view(margin=0)
    return viewer


if __name__ == '__main__':
    viewer = main()
    napari.run()
