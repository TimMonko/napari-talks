"""Build and maintain the roster behind the team map (``shared/data/team.toml``).

    pixi run -e demos team-roster    # parse napari/docs team.md into team.toml
    pixi run -e demos team-geocode   # fill lat/lon for each `place` (once)
    pixi run -e demos team-avatars   # cache github.com/<handle>.png avatars

Re-running ``roster`` refreshes names and roles from napari/docs and keeps the
hand-filled fields (place, precision, lat, lon, photo). Locations are rounded
before they are stored; nobody's position is more precise than a city.
"""

import re
import sys
import tomllib
import urllib.request
from pathlib import Path

DATA = Path(__file__).parents[1] / "data"
TEAM_TOML = DATA / "team.toml"
AVATARS = DATA / "avatars"
TEAM_MD = "https://raw.githubusercontent.com/napari/docs/main/docs/community/team.md"

# team.md section heading -> (role, emeritus?)
SECTIONS = {
    "Current Core Team Members": ("core", False),
    "Emeritus Core Team Members": ("core", True),
    "Current Steering Council Members": ("sc", False),
    "Emeritus Steering Council Members": ("sc", True),
    "Current Triage Team Members": ("triage", False),
}
ROLE_ORDER = ["sc", "core", "triage"]
# rounding step in degrees per precision
ROUNDING = {"city": 0.5, "region": 1.0, "country": 1.0}
KEPT = ("place", "precision", "lat", "lon", "photo")

ENTRY = re.compile(r"^- \[(?P<name>[^\]]+)\]\(https://github\.com/[^)]*\)\*? -\s+\[@(?P<handle>[\w-]+)\]", re.M)


def parse_team_md(text: str) -> dict[str, dict]:
    people: dict[str, dict] = {}
    for section in re.split(r"^## ", text, flags=re.M)[1:]:
        heading, _, body = section.partition("\n")
        if heading.strip() not in SECTIONS:
            continue
        role, emeritus = SECTIONS[heading.strip()]
        # entries wrap onto a second line; join them first
        body = re.sub(r" -\n\s+\[@", " - [@", body)
        for m in ENTRY.finditer(body):
            p = people.setdefault(m["handle"], {"handle": m["handle"], "name": m["name"], "roles": [], "emeritus": []})
            (p["emeritus"] if emeritus else p["roles"]).append(role)
    for p in people.values():
        p["roles"] = [r for r in ROLE_ORDER if r in p["roles"]]
        # a current role supersedes the emeritus one (e.g. emeritus SC, current core)
        p["emeritus"] = [r for r in ROLE_ORDER if r in p["emeritus"] and r not in p["roles"]]
    return people


def load() -> list[dict]:
    if not TEAM_TOML.exists():
        return []
    return tomllib.loads(TEAM_TOML.read_text(encoding="utf-8"))["person"]


def _value(v) -> str:
    if isinstance(v, str):
        return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(v, list):
        return "[" + ", ".join(_value(x) for x in v) + "]"
    return repr(v)


def save(people: list[dict]) -> None:
    lines = [
        "# napari team roster for the team map (shared/demos/team_map.py).",
        "# Names, handles and roles come from napari/docs docs/community/team.md",
        "# (refresh: `pixi run -e demos team-roster`; hand-filled fields are kept).",
        '# Fill `place` ("Kyoto", "Bavaria, Germany", "Japan") and `precision`',
        "# (city | region | country), then `pixi run -e demos team-geocode`.",
        "# lat/lon are rounded; `photo` is a path under shared/data/ for anyone",
        "# without a GitHub avatar. Leave `place` empty to appear off the map.",
        "",
    ]
    for p in people:
        lines.append("[[person]]")
        for key in ("handle", "name", "roles", "emeritus", "place", "precision", "lat", "lon", "photo"):
            if key in p and p[key] is not None:
                lines.append(f"{key} = {_value(p[key])}")
        lines.append("")
    TEAM_TOML.write_text("\n".join(lines), encoding="utf-8")


def roster() -> None:
    with urllib.request.urlopen(TEAM_MD) as r:
        parsed = parse_team_md(r.read().decode())
    old = {p["handle"]: p for p in load()}
    people = []
    for handle, p in parsed.items():
        prev = old.get(handle, {})
        p.update({k: prev[k] for k in KEPT if k in prev})
        p.setdefault("place", "")
        p.setdefault("precision", "")
        p.setdefault("photo", "")
        people.append(p)
    rank = lambda p: (ROLE_ORDER.index((p["roles"] or p["emeritus"])[0]), bool(not p["roles"]), p["name"].lower())
    people.sort(key=rank)
    DATA.mkdir(parents=True, exist_ok=True)
    save(people)
    dropped = sorted(set(old) - set(parsed))
    print(f"wrote {len(people)} people to {TEAM_TOML}" + (f"; dropped {dropped}" if dropped else ""))


def geocode() -> None:
    from geopy.geocoders import Nominatim

    # only the place text is sent, never a name or handle
    geo = Nominatim(user_agent="napari-team-map")
    people = load()
    for p in people:
        if not p.get("place") or "lat" in p:
            continue
        loc = geo.geocode(p["place"], timeout=10)
        if loc is None:
            print(f"  not found: {p['place']!r} ({p['handle']})")
            continue
        step = ROUNDING.get(p.get("precision") or "city", 0.5)
        p["lat"] = round(round(loc.latitude / step) * step, 1)
        p["lon"] = round(round(loc.longitude / step) * step, 1)
        print(f"  {p['place']}: {p['lat']}, {p['lon']}")
    save(people)


def avatars(size: int = 160) -> None:
    AVATARS.mkdir(parents=True, exist_ok=True)
    for p in load():
        out = AVATARS / f"{p['handle']}.png"
        if out.exists():
            continue
        url = f"https://github.com/{p['handle']}.png?size={size}"
        with urllib.request.urlopen(url) as r:
            out.write_bytes(r.read())
        print(f"  {out.name}")


if __name__ == "__main__":
    {"roster": roster, "geocode": geocode, "avatars": avatars}[sys.argv[1]]()
