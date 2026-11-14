# napari talks

Slides for talks about [napari](https://napari.org), built with [Quarto](https://quarto.org) and reveal.js and published to GitHub Pages on every push to `main`.

## Write a talk

```sh
uv sync                                   # matplotlib, pandas, jupyter for plot cells
uv run quarto preview talks/2026-08-euroscipy-sample/index.qmd
```

Copy `talks/2026-08-euroscipy-sample/` to `talks/<yyyy-mm-venue>/`, edit `index.qmd`, and add the talk to the list in `index.qmd` at the root. Press **T** in a deck to switch light / dark (or add `?theme=light` to the URL).

## Layout

| Path | What lives there |
| --- | --- |
| `_extensions/napari/` | the theme (`format: napari-revealjs`): layout grid, colours, IBM Plex, logos, slide chrome, live-demo hook |
| `shared/style/` | matplotlib styles and `napari_plots.py`, so every plot matches the slides in both themes |
| `shared/demos/` | napari scripts for live demos; `screenshots.py` regenerates UI screenshots from code |
| `shared/runner/` | tiny local server that lets a slide's **Launch napari** button start a demo script |
| `shared/media/` | reused media (MP4 recordings, screenshots) |
| `shared/slides/` | reusable slides, pulled in with `{{< include ../../shared/slides/community.qmd >}}` |
| `talks/<date-venue>/` | one folder per talk, with its own `data/` snapshot |

## Live demos

```sh
uv sync --group demos
uv run python shared/runner/runner.py     # listens on 127.0.0.1:8765
```

With the runner going, demo slides show a **Launch napari** button that opens the script on your machine. Without it (for example on the published site) they play the recorded MP4 instead.

Regenerate screenshots after a napari UI change:

```sh
uv run shared/demos/screenshots.py
```

## Publishing

`.github/workflows/publish.yml` renders every talk (re-running the plot cells) and deploys `_site/` to GitHub Pages. Pull requests build but don't deploy. In the repository settings, set **Pages → Source** to **GitHub Actions**.
