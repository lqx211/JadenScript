"""The anim3d renderer, its embedding tool and the checked-in copies stay in step."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "anim3d" / "core.jsw"
HOST = ROOT / "examples" / "anim3d_host.jsw"
PLAYER = ROOT / "userspace" / "wem_np4_26b5" / "wem_np4_26b5.jsw"

sys.path.insert(0, str(ROOT / "tools"))
import decode_linux_font  # noqa: E402
import embed_anim3d  # noqa: E402
from tests.tools_numbers import (  # noqa: E402
    key_ticks,
    shot_captions,
    shot_motifs,
    shot_ticks,
    total_ticks,
)


def _project(path: Path) -> dict:
    with zipfile.ZipFile(path) as z:
        import json

        return json.loads(z.read("project.json"))


class EngineSourceTests(unittest.TestCase):
    def test_engine_compiles(self) -> None:
        out = ROOT / "build" / "anim3d_test.sb3"
        out.parent.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(
            [sys.executable, "-m", "jswc", "build", str(ENGINE), "-o", str(out)],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("  ⚠", r.stdout, r.stdout)
        self.assertTrue(out.exists())

    def test_every_global_is_namespaced(self) -> None:
        """Nothing the renderer owns may shadow a host variable."""
        out = ROOT / "build" / "anim3d_test.sb3"
        proj = _project(out)
        stage = next(t for t in proj["targets"] if t["isStage"])
        names = [v[0] for v in stage["variables"].values()]
        names += [v[0] for v in stage["lists"].values()]
        loose = [n for n in names if not n.startswith("D3.") and n != "Tick.tick"]
        self.assertEqual(loose, [], "globals must be D3.* or Tick.tick")

    def test_shot_and_key_lists_are_complete_rows(self) -> None:
        text = ENGINE.read_text(encoding="utf-8")
        shots = re.search(r"public D3\.shots = \[(.*?)\];", text, re.S)
        self.assertIsNotNone(shots)
        shipped = [ln for ln in shots.group(1).splitlines() if ln.split("//", 1)[0].strip(", ")]
        self.assertEqual(len([r for r in shipped if not r.strip().startswith("//")]), len(shot_ticks()),
                         "every shot row needs tick, motif, fade, param and a caption")
        self.assertEqual(len(shot_captions()), len(shot_ticks()))

        keys = re.search(r"public D3\.keys = \[(.*?)\];", text, re.S)
        self.assertIsNotNone(keys)
        rows = [n.strip() for n in keys.group(1).replace("\n", " ").split(",") if n.strip()]
        self.assertEqual(len(rows) % 5, 0, rows)

    def test_show_data_matches_the_generator(self) -> None:
        r = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "gen_anim3d_show.py"), "--check"],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_shot_and_key_rows_line_up(self) -> None:
        shots = shot_ticks()
        keys = key_ticks()
        total = total_ticks()
        self.assertEqual(shots[0], 0, "the show has to start at tick 0")
        self.assertEqual(keys[0], 0)
        self.assertEqual(shots[-1], total, "the last shot marker lands on the end")
        self.assertEqual(keys[-1], total, "the last key lands on the end")
        self.assertEqual(shots, sorted(shots), "shots must be in clock order")
        self.assertEqual(len(set(shots)), len(shots), "two shots on one tick never resolve")

    def test_every_motif_id_is_known(self) -> None:
        known = set(range(21))
        unknown = sorted(set(shot_motifs()) - known)
        self.assertEqual(unknown, [], f"shot list names motifs {unknown} that drawScene cannot draw")
        self.assertGreater(len(set(shot_motifs())), 10, "the film should not sit on a handful of motifs")

    def test_a_longer_keyword_is_never_swallowed_by_a_shorter_one(self) -> None:
        """``dialogs`` must not become a clock just because it contains ``dial``.

        ``motif_for`` used to take the first keyword in table order, so every
        entry that contained a shorter, earlier keyword was unreachable --
        ``dialogs`` -> dial (the clock gauge, drawn in the wrong section),
        ``unroll`` -> helix and ``string`` -> rings.
        """
        sys.path.insert(0, str(ROOT / "tools"))
        import gen_anim3d_show as show

        self.assertEqual(show.motif_for("dialogs", "bridge"), "glyphs")
        self.assertEqual(show.motif_for("dialogs2", "bridge"), "glyphs")
        self.assertEqual(show.motif_for("unroll", "intro"), "wave")
        self.assertEqual(show.motif_for("string", "v2"), "wave")

    def test_one_keyword_cannot_be_trusted_for_two_different_shots(self) -> None:
        """``intro/panel`` is a code panel; ``c2/panel`` is an instrument panel."""
        sys.path.insert(0, str(ROOT / "tools"))
        import gen_anim3d_show as show

        self.assertEqual(show.motif_for("panel", "intro"), "glyphs")
        self.assertEqual(show.motif_for("panel", "c2"), "dial")
        self.assertEqual(show.motif_for("clock", "pre2"), "dial")

    def test_the_ad_to_bc_pair_lands_on_the_sung_words(self) -> None:
        """`trails` starts on "A.D" and `trailsTop` on "B.C", not on a guess.

        These two shots carry one lyric line between them: the particles run one
        way over "To A.D", then reverse on "to B.C". The generator resolves the
        chapter's `keys(T)` symbols against the lyric table, so the split has to
        fall exactly on the word times (53.562 s / 54.742 s).
        """
        sys.path.insert(0, str(ROOT / "tools"))
        import gen_anim3d_show as show

        ticks = shot_ticks()
        motifs = shot_motifs()
        rate = 34.6664
        pairs = [(t / rate, m) for t, m in zip(ticks, motifs) if m in (19, 20)]
        self.assertEqual([m for _, m in pairs], [19, 20], pairs)
        self.assertAlmostEqual(pairs[0][0], 53.562, delta=0.05)
        self.assertAlmostEqual(pairs[1][0], 54.742, delta=0.05)

    def test_the_trails_pair_survives_a_regen(self) -> None:
        r = subprocess.run(
            [sys.executable, str(ROOT / "tools" / "gen_anim3d_show.py"), "--check"],
            cwd=ROOT, capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_the_clock_gauge_never_shows_up_before_its_section(self) -> None:
        """The dial motif is the film's clock; the intro has no clock in it."""
        text = ENGINE.read_text(encoding="utf-8")
        names = re.search(r'public D3\.sectionNames = "([^"]*)";', text).group(1).split()
        body = text.split("public D3.sections = [", 1)[1].split("];", 1)[0]
        vals = [float(v) for ln in body.splitlines()
                for v in ln.split("//", 1)[0].replace(",", " ").split()]
        starts = dict(zip(names, vals[0::2]))
        pre2 = int(starts["PRE2"])

        dial = 14
        early = [(t, m) for t, m in zip(shot_ticks(), shot_motifs()) if m == dial and t < pre2]
        # c1's gauge shots are real gauges and come before the clock section
        self.assertTrue(all(2_000 < t < pre2 for t, _ in early),
                        f"the clock gauge appears too early: {early}")
        self.assertEqual([t for t, _ in early], sorted(t for t, _ in early))

    def test_font_table_matches_the_terminal_font(self) -> None:
        """`D3.gfont` must be exactly what Linux.jsw's glyph walks produce."""
        text = ENGINE.read_text(encoding="utf-8")
        table = re.search(r"public D3\.gfont = \[(.*?)\];", text, re.S)
        self.assertIsNotNone(table)
        rows = [int(n) for n in table.group(1).replace("\n", " ").split(",") if n.strip()]
        self.assertEqual(rows, decode_linux_font.decode_font(),
                         "D3.gfont drifted from assets/lists/font.jsa")

    def test_font_charset_lines_up_with_the_table(self) -> None:
        text = ENGINE.read_text(encoding="utf-8")
        chars = re.search(r'public D3\.glyphChars = "(.*)";', text)
        self.assertIsNotNone(chars)
        literal = chars.group(1).replace('\\"', '"').replace("\\\\", "\\")
        self.assertEqual(literal, "".join(chr(c) for c in range(32, 127)))


class EmbedTests(unittest.TestCase):
    def test_extraction_drops_the_demo_only_parts(self) -> None:
        block = embed_anim3d.engine_block(ENGINE)
        self.assertNotRegex(block, r"(?m)^public Tick\.tick")
        self.assertNotIn("\nstage {", block)
        self.assertNotIn("_twconfig_", block)
        self.assertIn("class D3 {", block)
        self.assertIn('D3: name="D3"', block)

    def test_splice_is_idempotent(self) -> None:
        block = embed_anim3d.engine_block(ENGINE)
        once = embed_anim3d.splice("public Tick.tick = 0;\n", block)
        twice = embed_anim3d.splice(once, block)
        self.assertEqual(once, twice)
        self.assertEqual(once.count(embed_anim3d.BEGIN), 1)

    def test_host_fixture_is_current(self) -> None:
        self.assertTrue(HOST.exists(), HOST)
        self.assertEqual(
            embed_anim3d.splice(HOST.read_text(encoding="utf-8"), embed_anim3d.engine_block(ENGINE)),
            HOST.read_text(encoding="utf-8"),
            "examples/anim3d_host.jsw is stale: run `make anim3d-embed`",
        )

    def test_noteplayer_copy_is_current(self) -> None:
        if not PLAYER.exists():
            self.skipTest(f"{PLAYER} is not present")
        self.assertEqual(
            embed_anim3d.splice(
                PLAYER.read_text(encoding="utf-8"), embed_anim3d.engine_block(ENGINE)
            ),
            PLAYER.read_text(encoding="utf-8"),
            "userspace/wem_np4_26b5/wem_np4_26b5.jsw is stale: run `make anim3d-embed`",
        )

    def test_embedding_does_not_duplicate_the_clock(self) -> None:
        """`Tick.tick` belongs to the host; the renderer only reads it."""
        if not PLAYER.exists():
            self.skipTest(f"{PLAYER} is not present")
        text = PLAYER.read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"(?m)^public Tick\.tick\b", text)), 1)
        self.assertIn("public void D3.afterFrame()", text)
        self.assertIn("if (D3.active == 0) {", text)

    def test_graph_readout_can_actually_draw(self) -> None:
        """The pen read-out needs its glyph table *and* a real stroke per pixel.

        ``Graph.nfont`` was declared by monitor metadata alone, so the rebuilt
        project had a 28-item font that was empty: ``indexOf`` returned 0 and no
        digit ever inked. A pixel also has to be a one-pixel ``gotoxy`` stroke --
        ``penDown`` with no movement draws nothing.
        """
        if not PLAYER.exists():
            self.skipTest(f"{PLAYER} is not present")
        text = PLAYER.read_text(encoding="utf-8")

        expected = {"Graph.nfont": 30, "Graph.nfontw": 30}
        original = PLAYER.with_suffix(".sb3")
        if original.exists():
            with zipfile.ZipFile(original) as z:
                proj = json.loads(z.read("project.json"))
            for target in proj["targets"]:
                for lst in target.get("lists", {}).values():
                    if lst[0] in expected:
                        expected[lst[0]] = lst[1]

        for name, want in expected.items():
            m = re.search(rf"(?m)^public {re.escape(name)} = (\[.*\]);$", text)
            self.assertIsNotNone(m, f"{name} has no initial items")
            got = json.loads(m.group(1))
            if isinstance(want, int):
                # the reference .sb3 is not always around; the count still bites
                self.assertEqual(len(got), want, f"{name} lost items")
            else:
                self.assertEqual(got, want, f"{name} does not match the source project")

        body = text[text.index("private void drawNumber("):]
        body = body[: body.index("\n    }")]
        self.assertRegex(
            body, r"Pen\.setPenSize\(1\);",
            "glyph columns are 1px apart: a wider pen merges them and the read-out turns to mush",
        )
        self.assertEqual(
            len(re.findall(r"Motion\.setXY\(", body)),
            1,
            "a glyph pixel is one point: penDown stamps it through renderer.penPoint, "
            "so an extra stroke doubles the dot and fuses the 6px grid into a smear",
        )

if __name__ == "__main__":
    unittest.main()
