# Third-party material

This repository is MIT licensed (see [`LICENSE`](./LICENSE)) **except** for the
two subtrees below, which are somebody else's work and keep their own terms.
Neither is linked into the `jswc` compiler; they are aggregated here.

## `sample.jsw` — NotePlayer 4

A decompilation of **NotePlayer 4**, used as the compatibility fixture that
every block shape in the compiler was verified against: 828 lines, 3 classes,
44 functions, 1336 blocks, and the proof that `sample.jsw → .sb3 → .jsw → .sb3`
is canonically identical.

**Licence: GPL-3.0.** The archive it was decompiled from
(`sample_np4.26_sb3/`, 167 MB) is not in this repository.

## `wem_example/` — `world.execute (me) ;`

**FrostNova's** music video for Mili's song, kept because `anim3d`'s shot list
and keyframes are generated from its timeline (`make anim3d-show`).

**Licence: AGPL-3.0-or-later**, and it carries
[its own notice](./wem_example/NOTICE.md), which is the authoritative one. That
notice spells out material that is **not licensed at all** and to which this
repository grants no rights: the song and its recording, the lyrics (including
the official Chinese translation), and the names and marks of Mili and of
Anthropic. If you hold rights in that material and object to its presence,
open an issue and it will be removed.

## Everything else

The compiler (`jswc/`), the tools, the tests, the two machines
(`userspace/i386/`, `userspace/rv64/`), the editor support and the
documentation are MIT. The fonts and glyph tables under `assets/` are CP437 and
ASCII bitmaps assembled from the IBM PC character set that the two machines
emulate; the `.jsa` fixtures in `assets/lists/` are data extracted from the
NotePlayer fixture above and follow it.
