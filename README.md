# napari talks

Slides for talks about [napari](https://napari.org), built with [Quarto](https://quarto.org) and reveal.js and published to GitHub Pages on every push to `main`.

## Write a talk

Environments come from [pixi](https://pixi.sh), Quarto included (from conda-forge). Plots come from [nps](https://github.com/TimMonko/nps), expected as a sibling clone at `../nps`.

```sh
pixi run preview talks/2026-08-euroscipy-sample/index.qmd
```

Copy `talks/2026-08-euroscipy-sample/` to `talks/<yyyy-mm-venue>/`, edit `index.qmd`, and add the talk to the list in `index.qmd` at the root. Press **T** in a deck to switch light / dark (or add `?theme=light` to the URL).

## Layout

| Path | What lives there |
| --- | --- |
| `_extensions/napari/` | the theme (`format: napari-revealjs`): layout grid, colours, IBM Plex, logos, slide chrome, live-demo hook |
| `shared/style/` | matplotlib styles and `napari_plots.py` (older talks; new ones use `nps.plots.save()`) |
| `shared/demos/` | napari scripts for live demos; `screenshots.py` regenerates UI screenshots from code |
| `shared/runner/` | tiny local server that lets a slide's **Launch napari** button start a demo script |
| `shared/media/` | reused media (MP4 recordings, screenshots) |
| `shared/slides/` | reusable slides, pulled in with `{{< include ../../shared/slides/community.qmd >}}` |
| `talks/<date-venue>/` | one folder per talk, with its own `data/` snapshot |

## Live demos

```sh
pixi run -e demos runner                  # listens on 127.0.0.1:8765
```

With the runner going, demo slides show a **Launch napari** button that opens the script on your machine. Without it (for example on the published site) they play the recorded MP4 instead.

Regenerate screenshots after a napari UI change:

```sh
pixi run -e demos screenshots
```

## Publishing

`.github/workflows/publish.yml` checks out `TimMonko/nps` beside this repo, renders every talk with `pixi run render` (re-running the plot cells) and deploys `_site/` to GitHub Pages. Pull requests build but don't deploy. In the repository settings, set **Pages → Source** to **GitHub Actions**.
