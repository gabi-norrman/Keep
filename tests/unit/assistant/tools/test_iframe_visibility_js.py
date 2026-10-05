"""The iframe visibility helper shared by the snapshot collector and the per-frame
pre-pass, run in node against hand-built element chains."""
import json
import shutil
import subprocess

import pytest

from assistant.tools.WebAccess import WebAccess

# A minimal stand-in for the DOM the helper touches: elements with a computed
# style, a box and a parent; a window with a viewport; a document whose
# elementFromPoint answers with whatever the case says is painted on top.
HARNESS = r"""
const DEF = { display: 'block', visibility: 'visible', opacity: '1', overflowX: 'visible', overflowY: 'visible' };
const win = { innerWidth: 1000, innerHeight: 800, getComputedStyle: e => e.cs };
let onTop = null;
const doc = { body: null, elementFromPoint: () => onTop };
function mk(tag, rect, cs, parent) {
    const [left, top, width, height] = rect;
    return { tagName: tag.toUpperCase(), parentElement: parent || null, ownerDocument: doc,
             cs: Object.assign({}, DEF, cs || {}),
             getBoundingClientRect: () => ({ left, top, width, height, right: left + width, bottom: top + height }) };
}
doc.body = mk('body', [0, 0, 1000, 3000]);
const out = {};
let f;
f = mk('iframe', [100, 100, 494, 130], {}, mk('div', [100, 100, 494, 130], {}, doc.body));
out.plain = iframeHiddenBy(f, win);
f = mk('iframe', [100, 100, 300, 8], {}, mk('div', [100, 100, 300, 8], {}, doc.body));
out.tiny = iframeHiddenBy(f, win);
f = mk('iframe', [100, 100, 300, 58], {}, mk('div', [100, 100, 300, 58], { opacity: '0' }, doc.body));
out.transparent = iframeHiddenBy(f, win);
f = mk('iframe', [100, 600, 494, 130], {}, mk('div', [100, 600, 494, 0], {}, doc.body));
onTop = mk('span', [100, 650, 100, 20], {}, doc.body);
out.covered = iframeHiddenBy(f, win);
onTop = f;
out.floated = iframeHiddenBy(f, win);
f = mk('iframe', [100, 2000, 494, 130], {}, mk('div', [100, 2000, 494, 0], {}, doc.body));
onTop = f;
out.offscreen = iframeHiddenBy(f, win);
f = mk('iframe', [100, 600, 494, 130], {}, mk('div', [100, 100, 494, 60], { overflowY: 'hidden' }, doc.body));
out.clipped = iframeHiddenBy(f, win);
f = mk('iframe', [100, 100, 494, 130], {}, mk('span', [0, 0, 0, 0], { display: 'inline' }, mk('div', [100, 100, 494, 130], {}, doc.body)));
out.inline = iframeHiddenBy(f, win);
console.log(JSON.stringify(out));
"""


@pytest.mark.unit
class TestIframeHiddenBy:
    @pytest.fixture(scope='class')
    def verdict(self):
        node = shutil.which('node')
        if not node:
            pytest.skip('node is not installed')
        run = subprocess.run([node, '-e', WebAccess._IFRAME_HIDDEN_BY_JS + HARNESS],
                             capture_output=True, text=True, timeout=20)
        assert run.returncode == 0, run.stderr
        return json.loads(run.stdout)

    def test_a_rendered_frame_is_shown(self, verdict):
        assert verdict['plain'] == ''

    def test_a_frame_with_no_box_of_its_own_is_hidden(self, verdict):
        assert verdict['tiny']

    def test_an_opacity_zero_ancestor_hides(self, verdict):
        assert 'opacity' in verdict['transparent']

    def test_a_collapsed_row_hides_when_something_else_paints_at_the_centre(self, verdict):
        assert 'collapsed' in verdict['covered'] and 'covered' in verdict['covered']

    def test_a_collapsed_row_does_not_hide_a_frame_that_paints(self, verdict):
        # the 0px float-container shape: same ancestor, but the hit-test reaches the frame
        assert verdict['floated'] == ''

    def test_a_collapsed_row_off_screen_counts_as_collapsed(self, verdict):
        assert 'collapsed' in verdict['offscreen'] and 'covered' not in verdict['offscreen']

    def test_a_clipping_ancestor_that_does_not_reach_the_frame_hides(self, verdict):
        assert 'clipped' in verdict['clipped']

    def test_inline_ancestors_are_skipped(self, verdict):
        assert verdict['inline'] == ''


@pytest.mark.unit
class TestCollectorChoiceGuards:
    """Tripwires for the collector script, which only a browser can run."""

    def test_a_hidden_radio_is_listed_through_its_label(self):
        js = WebAccess._BROWSE_COLLECT_JS
        loop = js.index('for (const el of els) {')
        assert js.index('hiddenChoiceHost(el, win)', loop) < js.index('records.push({ rec, el: target })')

    def test_a_wrapping_label_names_form_controls_only(self):
        js = WebAccess._BROWSE_COLLECT_JS
        label_fn = js.index('function label(el)')
        guard = js.index("tagName === 'input' || tagName === 'select' || tagName === 'textarea'", label_fn)
        assert guard < js.index("el.closest('label')", label_fn)

    def test_both_scripts_share_the_visibility_helper(self):
        assert 'function iframeHiddenBy' in WebAccess._BROWSE_COLLECT_JS
        assert 'function iframeHiddenBy' in WebAccess._FRAME_CHILD_IFRAMES_JS
