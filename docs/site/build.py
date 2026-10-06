"""Builds the public readme page: docs/site/out/index.html, the readme as GitHub renders it, with the images it shows.
The repository is private, so this page — the root of the GitHub Pages site, the Keep Atlas beside it under atlas/ — is
how anyone outside it reads the readme. Standard library only, so CI runs it without the venv.

    python3 docs/site/build.py [--out DIR]

GitHub's Markdown API does the rendering (authenticated when GITHUB_TOKEN or GH_TOKEN is set, 60 requests an hour
otherwise). Only docs/images/ is published with the page, so the build exits 1 when the readme points at any other
local file; `problems()` is the same check for the unit test."""
import argparse
import json
import os
import re
import shutil
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
README = REPO / "readme.md"
PUBLISHED = "docs/images"
MARKDOWN_API = "https://api.github.com/markdown"

REFERENCE = re.compile(r"""(?:\b(?:src|href)=["']|\]\()([^"')\s]+)""")
EXTERNAL = re.compile(r"^([a-z][a-z0-9+.-]*:|//|#)", re.IGNORECASE)
CAMO = r'"https://camo\.githubusercontent\.com/[^"]*"'
CAMO_LINK = re.compile(rf'href={CAMO}(>\s*<img\b[^>]*? data-canonical-src="([^"]*)")')
CAMO_IMAGE = re.compile(rf'src={CAMO}([^>]*?) data-canonical-src="([^"]*)"')


def local_references(markdown: str) -> list[str]:
    """Every file of this repository the readme links to or shows, in order of appearance."""
    return [target for target in REFERENCE.findall(markdown) if not EXTERNAL.match(target)]


def problems(markdown: str, repo: Path = REPO) -> list[str]:
    """What would be broken on the public page: a local file outside docs/images/, or one that does not exist."""
    found = []
    for target in dict.fromkeys(local_references(markdown)):
        path = target.split("#")[0].split("?")[0]
        if not path.startswith(PUBLISHED + "/") or ".." in Path(path).parts:
            found.append(f"readme.md points at {target}: only {PUBLISHED}/ is published with the page")
        elif not (repo / path).is_file():
            found.append(f"readme.md points at {target}, which does not exist")
    return found


def canonical_images(html: str) -> str:
    """GitHub's renderer routes external images, and the links it wraps them in, through its camo proxy; the page
    loads them from where they live."""
    return CAMO_IMAGE.sub(r'src="\2"\1', CAMO_LINK.sub(r'href="\2"\1', html))


def render(markdown: str) -> str:
    request = urllib.request.Request(MARKDOWN_API, json.dumps({"text": markdown, "mode": "gfm"}).encode("utf-8"),
                                     {"Accept": "application/vnd.github+json", "User-Agent": "keep-readme-page"})
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=30) as response:
        return canonical_images(response.read().decode("utf-8"))


def page(readme_html: str) -> str:
    return (HERE / "template.html").read_text(encoding="utf-8").replace("__README__", readme_html)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=HERE / "out")
    args = parser.parse_args()
    markdown = README.read_text(encoding="utf-8")
    found = problems(markdown)
    for p in found:
        print(f"site: {p}", file=sys.stderr)
    if found:
        return 1
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "index.html").write_text(page(render(markdown)), encoding="utf-8")
    shutil.copytree(REPO / PUBLISHED, args.out / PUBLISHED, dirs_exist_ok=True)
    print(f"site: readme and {len(set(local_references(markdown)))} images → {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
