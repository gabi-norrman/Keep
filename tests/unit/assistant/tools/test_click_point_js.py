"""The click-point verdict behind browse_act's real mouse click, run in node against
hand-built elements: a point for a visible main-page element, else the reason — so a
page on which every click falls back to the JS click says why in the log (#82: a
Usercentrics shadow host over the whole of fedex.com gave one opaque line for four
different checks)."""
import json
import shutil
import subprocess

import pytest

from assistant.tools.WebAccess import WebAccess

HARNESS = r"""
const doc = {};
let onTop = null;
doc.elementFromPoint = () => onTop;
function el(tag, rect, opts) {
    opts = opts || {};
    const [left, top, width, height] = rect;
    const e = { tagName: tag.toUpperCase(), id: opts.id || '', className: opts.cls || '',
                ownerDocument: opts.doc || doc, shadowRoot: opts.shadow ? {} : null,
                children: [],
                getBoundingClientRect: () => ({ left, top, width, height }),
                contains(o) { return o === e || e.children.includes(o); },
                getRootNode: () => opts.inShadow ? {} : doc };
    return e;
}
globalThis.document = doc;
globalThis.window = { innerWidth: 1000, innerHeight: 800 };
const out = {};
let b = el('button', [100, 100, 200, 40], {cls: 'ask-fedex-btn'});
onTop = b;
out.visible = clickPoint(b);
onTop = el('aside', [0, 0, 1000, 800], {id: 'usercentrics-cmp-ui', shadow: true});
out.covered = clickPoint(b);
onTop = el('span', [0, 0, 0, 0]);
b.children.push(onTop);
out.ownChild = clickPoint(b);
out.offscreen = clickPoint(el('button', [100, 2000, 200, 40]));
out.noBox = clickPoint(el('button', [100, 100, 0, 0]));
out.framed = clickPoint(el('button', [100, 100, 200, 40], {doc: {}}));
out.gone = clickPoint(null);
console.log(JSON.stringify(out));
"""


@pytest.mark.unit
class TestClickPoint:
    @pytest.fixture(scope='class')
    def verdict(self):
        node = shutil.which('node')
        if not node:
            pytest.skip('node is not installed')
        program = 'const clickPoint = ' + WebAccess._CLICK_POINT_JS.strip() + ';' + HARNESS
        run = subprocess.run([node, '-e', program], capture_output=True, text=True, timeout=20)
        assert run.returncode == 0, run.stderr
        return json.loads(run.stdout)

    def test_a_visible_element_gets_its_centre(self, verdict):
        assert verdict['visible'] == {'x': 200, 'y': 120}

    def test_a_covering_layer_is_named(self, verdict):
        v = verdict['covered']
        assert v['reason'] == 'covered'
        assert v['hit'] == 'aside#usercentrics-cmp-ui'
        assert v['shadow'] is True
        assert v['target'] == 'button.ask-fedex-btn'

    def test_the_elements_own_child_on_top_does_not_cover_it(self, verdict):
        assert verdict['ownChild'] == {'x': 200, 'y': 120}

    def test_a_centre_outside_the_viewport_says_where_it_is(self, verdict):
        v = verdict['offscreen']
        assert v['reason'].startswith('centre outside')
        assert (v['x'], v['y']) == (200, 2020)

    def test_an_element_without_a_box_is_not_clickable(self, verdict):
        assert verdict['noBox']['reason'].startswith('no box')

    def test_a_frame_element_is_not_clicked_from_the_main_page(self, verdict):
        assert 'iframe' in verdict['framed']['reason']

    def test_a_missing_element_is_reported_gone(self, verdict):
        assert verdict['gone']['reason'] == 'element gone'
