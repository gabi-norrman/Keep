"""The public readme page (docs/site) carries everything the readme shows: the repository is private and CI publishes
only the page and docs/images/, so a readme pointing anywhere else in the tree is a broken image for every reader."""
import importlib.util

import pytest

from common.Paths import REPO_DIR


@pytest.fixture(scope="module")
def site():
    spec = importlib.util.spec_from_file_location("site_build", REPO_DIR / "docs" / "site" / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_readme_shows_only_what_the_public_page_carries(site):
    assert site.problems(site.README.read_text(encoding="utf-8")) == []


def test_a_file_outside_the_published_images_is_reported(site, tmp_path):
    (tmp_path / "docs" / "images").mkdir(parents=True)
    (tmp_path / "docs" / "images" / "lamp.png").touch()
    (tmp_path / "res").mkdir()
    (tmp_path / "res" / "logo.png").touch()
    markdown = ('<img src="docs/images/lamp.png"> <img src="res/logo.png"> ![plan](docs/images/../../res/logo.png) '
                "[install](install.sh)")
    found = site.problems(markdown, tmp_path)
    assert len(found) == 3
    assert not any("lamp.png" in p for p in found)


def test_a_missing_image_is_reported(site, tmp_path):
    assert any("gone.png" in p for p in site.problems("![gone](docs/images/gone.png)", tmp_path))


def test_links_that_leave_the_repository_are_not_local_references(site):
    markdown = ('[site](https://keep.example/) <a href="#privacy">privacy</a> [mail](mailto:m@mlab.test) '
                '<img src="//img.example/badge.svg">')
    assert site.local_references(markdown) == []


def test_proxied_images_load_from_their_origin(site):
    rendered = ('<a target="_blank" rel="noopener" href="https://camo.githubusercontent.com/75343552/68747470">'
                '<img src="https://camo.githubusercontent.com/75343552/68747470" alt="Runs on a Mac" '
                'data-canonical-src="https://img.example/badge/runs_on-Mac.svg" style="max-width: 100%;"></a>')
    html = site.canonical_images(rendered)
    assert 'src="https://img.example/badge/runs_on-Mac.svg"' in html
    assert 'href="https://img.example/badge/runs_on-Mac.svg"' in html
    assert "camo.githubusercontent.com" not in html
    assert 'alt="Runs on a Mac"' in html
