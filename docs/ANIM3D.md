# anim3d — 3D on the pen layer

A pen-only 3D renderer for JadenScript, organised the way `wem_example` is: the
picture is a **pure function of the song clock**, a shot list decides what is on
screen, keyframes drive the camera, and easing shapes the gap between keys.

No extension beyond `pen`, no costume, no sprite graphics. Every frame is a pile
of `motion_gotoxy` + `pen_penDown` line segments.

```sh
make anim3d                     # -> build/preview/anim3d.sb3
make anim3d-show                # regenerate the running order from wem_example
make anim3d-embed               # splice it into examples/anim3d_host.jsw
```

`anim3d/core.jsw` carries its own runtime settings as a metadata note, so the
standalone build opens at 180 FPS with Turbo Mode and the high-quality pen layer
already on:

```
metadata note Stage at 51,54 350x170: "{…}" // _twconfig_
```

## The clock is `Tick.tick`, never the frame counter

Turbo Mode runs the VM at 180 FPS; nothing in a scene may depend on how many
times the loop happened to run. Every pose is a function of `Tick.tick`, the
same variable `Ticker()` in NP4.26 advances once per beat subdivision:

```jsw
D3.showTick = (Tick.tick - D3.showOrigin) % D3.totalTicks;
D3.beatPhase = (D3.showTick % D3.ticksPerBeat) / D3.ticksPerBeat;
D3.pulseBeat = exp((0 - D3.beatPhase) * 5);      // 1 on the beat, decaying
```

With `Tick.wait = 4` a beat is `2 ** 4 = 16` ticks and a bar is 64. The show is
relative to `showOrigin`, captured on the green flag, so it looks the same
whether it runs standalone or inside a player whose clock is already at 2159. A
player reset moves the clock *backwards*: the renderer re-origins instead of
jumping.

### `Tick.tick` is the music clock; the picture runs on `showTick`

A tick is a sixteenth note — 34.6664 of them a second — but the pen is asked to
draw ~60 times a second. Taken literally, 25 of those frames a second would be
the same pose repeating, and anything that slides would judder. So the frame
clock carries a **fractional** position inside the current tick:

```jsw
if (_now != D3.lastTick) { D3.lastTick = _now; D3.tickAt = _clock; }
_frac = (_clock - D3.tickAt) * D3.tickRate;   // 0 → just under 1
D3.showTick = _now - D3.showOrigin + _frac;
```

`_frac` is clamped to `0.999`: a host that pauses its sequencer would otherwise
let the fraction run away. Nothing downstream needs to know — `shotAt`,
`applyKeys`, the easings and the motifs all take a fractional tick already, and
the beat clock gets `_now + _frac` so the pulses move smoothly too.

`Tick.tick` itself stays a whole number. It is the sequencer's clock, the thing
the player triggers notes against; only the picture interpolates between its
beats.

Three consequences, and they are the reason for the rule:

* **Deterministic.** The same tick produces the same picture at any frame rate,
  so a scene can be scrubbed, reversed, or captured by moving `Tick.tick` alone.
* **Cheap to re-time.** A shot's length is `shotT1 - shotT0`, not a frame count.
* **Frame-rate independent.** Dropping frames drops smoothness, never time.

### The test clock advances at `tickRate`, not once per frame

The standalone demo needs its own clock. It deliberately does **not** do
`Tick.tick += 1; wait(1/180)`: that is 180 ticks a second, and the show is 7346
ticks over 211.9 s, i.e. 34.6664 ticks a second — the whole film ran five times
too fast and every shot flashed past in a fifth of a second. It accumulates real
time and releases whole ticks instead, so the tempo is right at any frame rate:

```jsw
_acc += (Sensing.timer() - _last) * D3.tickRate;
_last = Sensing.timer();
while (_acc >= 1) { Tick.tick += 1; _acc -= 1; }
```

Inside NotePlayer this block does not exist — the player's own tick engine owns
`Tick.tick`, which is the whole point of reading a variable instead of counting
frames.

### Two gates in `frame()`
`frame()` is called once per VM frame and returns immediately unless **both**:

1. `Tick.tick` changed since the last redraw — the pose is stale otherwise.
2. at least `frameInterval` (1/60 s) passed on the wall clock — the redraw budget.

The wall-clock gate is what makes "180 FPS VM, 60 FPS backend" true: a redraw
costs ~60 pen operations, and there is no point paying that 180 times a second.
The comparison uses `frameInterval * 0.9` because a 60 FPS frame lands only
~16.7 ms after the previous one, and an exact threshold would drop every second
pose to timer jitter.

In NotePlayer the tick rate comes from the audio clock (~40 Hz at 148 BPM), so
the budget never trips there and `drawn` tracks `tick` one to one.

## The running order comes from the film

`anim3d` does not invent a list of scenes. It reads the one the example webgl
film already has: **15 chapters, 274 shots, 211.9 s**, and lays them over the
measured beat grid.

```sh
make anim3d-show      # regenerate the data block from wem_example
```

`tools/gen_anim3d_show.py` does three things:

1. **Reads the structure.** It walks `wem_example/src/ch/NN_*.js`, pulls out each
   chapter id and its `shots: [{ id, at }]`, and takes the section table
   (`intro`, `inst1`, `v1`, `pre1`, `c1`, …) from `wem_example/data/timing.json`.
2. **Resolves the clock.** Each chapter builds its timeline in a `keys(T)` helper
   and a shot's `at` reads a symbol back out of it, so the generator parses that
   helper instead of guessing:

   | `at` | resolved from |
   | --- | --- |
   | `keys(T).B(n)` | beat `b0 + n`, where `b0` is the beat the section starts on (the film's `B` is section-relative, not absolute) |
   | `keys(T).lNN.start` | the lyric line the chapter binds with `const lNN = L("text")` |
   | `keys(T).tXXX` | whatever the chapter assigns that symbol (`lNN.words[k].start`, `lNN.start`, `B(n)`, `T.section(..)`) |
   | `keys(T).s0` | the section's own start |
   | `(T) => 12.5` | seconds |

   Lyric lookup mirrors `src/engine/timing.js` exactly — substring match, "smart
   case" (a lower-case query matches any case), first hit in document order.
   **58 %** of the 260 shots now land on their real timestamp. Whatever is left
   is interpolated between the resolved neighbours inside its own section, so the
   order and spacing survive even where the expression depends on a runtime value.
   At 130 BPM and 16 ticks per beat the whole film is **7,346 ticks**.

   This matters for anything tied to a word: `pre1`'s A.D → B.C pair sits at
   53.562 s and 54.742 s because that is where the singer says them, not because
   of where they fall in the section.
3. **Names a motif per shot.** 16 wireframe motifs, chosen by the shot's own
   name (`crack*` → shards, `dial|gauge|clock` → dial, `helix|braid|roll` →
   helix, …) and by the chapter's subject when the name says nothing.

Nothing but the running order and the clock is taken from the example — the
motifs, the geometry and every line of JadenScript are ours.

The emitted block is fenced so the tool can rewrite it and nothing else:

```jsw
// >>> show-data BEGIN — generated by tools/gen_anim3d_show.py …
public D3.totalTicks = 7346;
public D3.tickRate = 34.6664;
public D3.shots = [
    // intro        0.00s ..   15.07s  (19 shots)
      0, 15,  9,  4,   // cursor
     27, 10,  9,  0,   // trace
     …
];
public D3.keys = [ … ];
// <<< show-data END
```

A shot row is `startTick, motifId, fadeTicks, param`. The show **ends**: past
`totalTicks` the renderer returns without touching the pen.

`tests/test_anim3d.py` runs the generator with `--check`, so editing the data
block by hand fails the suite rather than silently drifting from the film.

### The collapse transition

A cross-fade is impossible with a pen: there is no alpha, and drawing two scenes
doubles the stroke count. Instead the outgoing shot **shrinks to a point** over
the last `fade` ticks and the incoming one **grows out of it** over the first
`fade`, by scaling the projection:

```jsw
D3.wScale = 1 - D3.ease * 0.92;     // tail of a shot
D3.wScale = 0.08 + D3.ease * 0.92;  // head of the next one
```

`wScale` multiplies projected `x` and `y` inside `render()`, so it costs one
multiply per vertex and **exactly one scene is drawn per frame**.

A fade is clamped to a **quarter** of its own shot, not a half. At nine ticks in
and nine out, a twenty-tick shot would never reach full size and the fast
sections (`c2x`, `bridge`, `chant`, `inst2` — four-tick cuts) would read as one
long collapse.

## Keyframes

`D3.keys` holds rows of `tick, yaw, pitch, dist, easeId`, one per shot, generated
with the shot list. `kfAt(t)` scans for the row `t` is in and how far it has
crossed the gap to the next one; a clock before the first key holds it, a clock
past the last one holds that. `applyKeys` then mixes the three camera channels:

```jsw
D3.easeBy(D3.keys.get(_b + 4), D3.kfK);
D3.camYaw = D3.keys.get(_a + 1) + (D3.keys.get(_b + 1) - D3.keys.get(_a + 1)) * D3.ease;
```

The ease id lives on the **destination** key: the shape belongs to the move you
are making, not the one you came from. Yaw accumulates monotonically so the
camera never snaps back between shots.

## Easing

Scratch custom blocks cannot return a value (`E0051`), so every easing writes
the shared `D3.ease` slot. Read it immediately after the call.

| id | name | id | name |
| --- | --- | --- | --- |
| 0 | linear | 6 | inOutCubic |
| 1 | inQuad | 7 | inExpo |
| 2 | outQuad | 8 | outExpo |
| 3 | inOutQuad | 9 | inOutSine |
| 4 | inCubic | 10 | outBack |
| 5 | outCubic | 11 | outElastic |

`case _` is linear, so an unknown id degrades to a straight line instead of a
wrong curve. `exp()` is `e ^`, so `2 ** x` is written `exp(x * 0.6931472)`.

`hash01(n)` is a stable pseudo-random in `[0,1)` for an integer key — the same
key always gives the same value, which is what lets a scene have "random" detail
that survives a re-render at a different frame rate.

## Camera: orbit versus fly

The camera is a pinhole at `z = -camDist` looking down `+Z`:

```
yaw about Y, pitch about X, roll about Z, then divide by depth
depth < nearZ  ->  flag 0, and every edge touching it is skipped
```

That near-plane flag matters: a vertex crossing the camera flings its projection
to infinity and paints a stripe across the stage. `D3.clipR` is the second guard:
a vertex that projects more than that many pixels out (in stage px, default 900)
is dropped too, so a segment that straddles the plane cannot streak either.

### The whole scene is scaled for a 480 × 360 stage

The film renders a 1920 × 1080 canvas, so one design pixel is **0.25 stage
pixels**. `D3.camFocal` carries that factor: the film's 330 design px becomes
`82.5` stage px. It used to be left at 330, which drew every scene four times too
large — geometry spent most of its life off the edge of the stage, which read as
"lines floating everywhere". Anything measured in design pixels needs the same
0.25 somewhere.

### Never name a screen point after a camera term

`_sx` and `_sy` once held the projected point, but `_sy` had already been used
for `sin(yaw)`:

```jsw
_cy = cos(_ey); _sy = sin(_ey);      // sin yaw
...
_sy = _ry * _s + _shy;               // screen y -- overwrites sin yaw
```

The first vertex projected fine, the second inherited its screen y as the yaw
sine, and by the third the depth had collapsed through the near plane. Every
motif came out as spaghetti, and the input vertices were provably a perfect
decagon the whole time. Screen points are `_px` and `_py` now. Anything the loop
needs on the next pass must not be reused for output.

`D3.orbit` picks between two behaviours, per scene:

* **orbit = 1** — the camera really swings around the origin. Only for scenes
  that sit near it (the polyhedron, the cube).
* **orbit = 0** — the camera only sways (`yaw × 0.12`, `pitch × 0.35`). For
  scenes that run away along `+Z` (the floor, the tunnel, the sheet): a full
  turn would put most of their geometry *behind* the camera and blank the frame.

Each scene also sets `baseYaw`, `basePitch`, `viewBias` and `nearZ`, because a
floor wants to be looked down on and a tunnel wants the camera inside it.

The pitch sign is easy to get backwards. A floor point at `(x, -2, z)` must move
**up** the screen as `z` grows, which means rotating the world by `-pitch`:

```jsw
_y1 = _y * _cp + _z1 * _sp;
_z2 = _z1 * _cp - _y * _sp;
```

With the other sign the floor recedes downward and the whole scene collapses
into a band under the horizon.

## The motifs

Twenty-one wireframe motifs, `case`-dispatched on the shot's motif id. `par` is the
shot's own knob, so one motif appears ten different ways instead of one shape for
the whole film.

| id | motif | geometry | what it is |
| --- | --- | --- | --- |
| 0 | `buildPoly` | 12 v / 30 e | icosahedron, breathing on the beat |
| 1 | `buildGrid` | 32 v / 16 e | floor plane running away, posts for height |
| 2 | `buildTunnel` | 48 v / 48 e | six octagonal rings receding and turning |
| 3 | `buildWave` | 30 v / 49 e | sine sheet rippling on the beat |
| 4 | `buildCube` | 16 v / 28 e | two nested cubes, the inner one tumbling |
| 5 | `buildStars` | 44–62 v | point field streaming at the camera |
| 6 | `buildSphere` | 48 v / 45 e | latitude rings plus meridians |
| 7 | `buildRings` | 50 v / 50 e | concentric rings breathing out of plane |
| 8 | `buildNetwork` | 10 v / 20 e | drifting node graph, links only |
| 9 | `buildGlyphs` | 64 v / 32 e | wall of code ticks on a receding plane |
| 10 | `buildHelix` | 24 v / 27 e | double spiral with rungs |
| 11 | `buildCurve` | 44 v / 44 e | closed polar curve, counter-rotating pair |
| 12 | `buildShards` | 40 v / 20 e | fragments snapping apart on the beat |
| 13 | `buildLattice` | 27 v / 32 e | three stacked 3×3 grids joined at the corners |
| 14 | `buildDial` | 18 v / 15 e | gauge: ticks on an arc, needle, track |
| 15 | `buildArrow` | 16 v / 8 e | chevrons stacking up |
| 16 | `buildCursor` | read-out only | the blinking block on black (shot 1 of the film) |
| 17 | `buildTrace` | 120 v / 60 e | top-down: via grid, board growing by hop, power ring |
| 18 | `buildCurrent` | 280 v / 140 e | the same board with current pulses running |
| 19 | `buildTrails` | 472 v / 272 e | a ring with 200 particles running round it, clockwise, accelerating |
| 20 | `buildTrailsTop` | 472 v / 272 e | the same ring, reversed and accelerating harder |

`trails` / `trailsTop` are pre1's "To A.D to B.C". The film turns a whole star
dome (`src/ch/pre1/sky.js`: `th1 = -(2026 - year) * .00118`) and then looks up
the pole; a dome is thousands of points, so the pen version keeps only what the
eye reads — one flat circle with a band of travelling particles just inside it.
The band sits at `D3.trailBand` (0.80) of the ring radius because on the ring
itself both are the same pen colour and the particles vanish into the line. The
two shots are the reversal: `trails` ends on `-trailTurns * 360`, `trailsTop`
starts on that angle and runs the other way, so the hand-off is continuous.

`viewFlat()` exists for them: `render()` scales the orbit yaw by 0.12 and the
pitch by 0.35, `view()`'s damp cancels only the yaw, so a flat motif has to undo
the pitch itself or its circle turns into an ellipse as the keyframes move.

The first three shots of the film are built at full detail — see
[`ANIM3D-SHOTS.md`](./ANIM3D-SHOTS.md) for the element-by-element inventory.

### Text

The glyphs are **Linux.jsw's terminal font**, decoded. `assets/lists/font.jsa`
holds 65 536 run-length entries, one per BMP code point; ASCII `c` lives at index
`c`, because `drawChar` asks a 1-based Scratch list for `get(C + 1)`. Each entry
is walked down 16-pixel columns with the pen climbing from the bottom of the
cell, `0` standing for a run of 15 and a non-zero run toggling the pen *after* it
is drawn.

`tools/decode_linux_font.py` replays that walk offline and emits what
`D3.gfont` holds: 95 printable ASCII glyphs, **8 px wide and 15 rows tall**, one
entry per row as an 8-bit mask with the leftmost pixel in bit 7. Row 0 is the
bottom of the cell, row 14 the top. Running the tool with a string prints the
decoded block, which is the fastest way to check a change:

```
$ python3 tools/decode_linux_font.py "if (_now == 12)"
```

`textAt(str, x, y, sc, up)` draws a line at the pen, one `plot()` per run of set
pixels — a glyph costs one to twelve short segments instead of one per pixel.
`sc` is stage pixels per font pixel, and every call is multiplied by
`D3.textScale`, which is the knob to turn for size:

| `D3.textScale` | glyph | use |
| --- | --- | --- |
| 1 | 8 × 15 stage px | native, one font pixel per stage pixel |
| **0.5** (default) | 4 × 8 stage px | a normal-sized read-out on a 480 × 360 stage |
| 0.25 | 2 × 4 stage px | the film's own proportion — 1920 design px pressed into 480 |

`up = 1` picks the upper case glyph of a letter and `up = 0` the lower case one,
because Scratch compares strings without regard to case — a glyph cannot work
that out for itself.

`textAt()` writes into the world buffer; the read-out pass sets `D3.space = 1`
and the same routine writes straight to the pen at stage coordinates, after
`render()`, so the HUD never moves with the camera and is not erased.

`render()` opens with `Pen.eraseAll()` and then fills the stage with
`#05070a` under every shot: the film is dark throughout, and painting it per
shot left the rest of the show on a white backdrop.

#### Captions

Most of the film's shots end with `overlays(ctx, K, { br: "view  top ·
orthographic" })`, the small right-aligned label at the bottom edge. The
generator scrapes that `br:` string out of every shot body and writes it as a
**fifth column** of `D3.shots`:

```
   27, 17,  9,  0, "view  top . orthographic",   // trace
```

`shotAt()` copies it into `D3.shotCaption`, `frame()` raises `D3.hud` whenever
it is non-empty, and `drawHud()` draws it right-aligned at
`y = -172`, `x = 232 - length * cellW * textScale` so the label stops a fixed
margin from the right edge instead of drifting with its length.

Two consequences worth knowing:

* the read-out font is ASCII 32–126, so `tools/gen_anim3d_show.py` transliterates
  the film's typography (`·` → `.`, `×` → `x`, `…` → `...`, curly quotes) before
  emitting; otherwise the character has no glyph and simply disappears.
* only **70** shots in the whole film carry a `br:` caption; the rest call
  `overlays(ctx, K)` bare, which is the frame with no label. Before this was
  wired up the engine only drew text in shots 16/17/18 — 163 of 7346 ticks,
  2.2 % of the show. With the caption column it draws in 37 of 260 shots,
  13.8 % of the runtime.

Adding one: write `buildX(t, par)`, add a `case` to `drawScene`, and give it a
keyword in the generator's table.

### Two knobs every motif has to set

`view(orbit, pitch, bias, near, damp)` — one call, so a motif can never inherit
the previous shot's clipping or pitch:

* `orbit = 1` swings the camera right around the origin; only for volumetric
  motifs near it.
* `orbit = 0` only sways (`yaw × 0.12`, `pitch × 0.35`), for anything that runs
  away along `+Z`.
* `damp` **cancels the keyframe yaw**, by `baseYaw = -camYaw × damp`. A flat
  motif (the glyph wall, the dial, the rings, the polar curve) that kept the full
  sway ends up edge-on for a third of the film and draws almost nothing — glyphs
  averaged 427 pixels before this, 3,623 after.

## Buffering

```
D3.pos    x,y,z,x,y,z,...        model space, rebuilt every frame
D3.idx    a,b, a,b, ...          1-based vertex indices, static per scene
D3.proj   sx,sy,depth,flag,...   4 slots per vertex, rebuilt every frame
```

One pass over `pos` fills `proj`; the edge loop only reads it. That is O(V + E)
instead of O(E), and a shared corner is transformed once instead of once per
edge.

`v3(x,y,z)` and `e2(a,b)` append to the current scene and keep `nv`/`ne`, so a
builder can name the last two vertices it wrote: `v3(…); v3(…); e2(nv - 1, nv);`.

### `prepare()` exists because `set` does not append

`data_replaceitemoflist` on an empty list is a **no-op**; every later read then
answers `""` and the arithmetic silently becomes `NaN`. `list.set(i, v)` can
therefore never grow a buffer. `prepare()` fills `pos`, `idx` and `proj` with
`add` up to their final length once, at startup, and the frame loop only ever
replaces items.

This is the single most common way a pen engine "draws nothing": the projection
buffer looks initialised but has length 0.

## Embedding in NotePlayer

```sh
python3 tools/embed_anim3d.py userspace/wem_np4_26b5/wem_np4_26b5.jsw
```

The tool copies `anim3d/core.jsw` into the target between two markers, first
removing the three things only the standalone demo should own — its
`public Tick.tick` declaration, its test clock `stage { }` block, and the
`_twconfig_` note. Re-running replaces the previous copy instead of appending a
second one; `--check` fails if the copy is stale, which is what
`tests/test_anim3d.py` uses to keep the checked-in project honest.

Every name the renderer owns is a Stage global prefixed `D3.` (`D3.pos`,
`D3.camYaw`, …), so nothing can collide with the host's variables. `Tick.tick`
is the only thing it reads that it does not own.

### The pen layer is project-wide

`scratch3_pen` keeps **one** `_penSkinId` for the whole VM — pen strokes from
every sprite land on the same layer. So `Pen.eraseAll()` from the renderer wipes
whatever else is drawn, including NotePlayer's piano keyboard.

The renderer therefore exposes two switches:

| variable | meaning |
| --- | --- |
| `D3.active` | `0` stops the renderer and leaves the pen completely alone |
| `D3.onFrame` | `1` broadcasts `D3.afterFrame` once per rendered frame |

and a host that draws its own overlay answers with:

```jsw
public void D3.afterFrame() { // hook: broadcast D3.afterFrame
    Piano.draw();
}
```

`Piano.draw()` in turn asks `D3.active` before clearing, because the renderer
already cleared once this frame:

```jsw
if (D3.active == 0) {
    Pen.eraseAll();
}
```

That is the whole contract: **whoever clears, clears once, and whoever sits on
top redraws after every frame.** In the combined build the stage green flag
does:

```jsw
public void anim3dStart() { // hook: green flag
    D3.active = 1;
    D3.onFrame = 1;
}
```

Neither switch is on by default in `anim3d/core.jsw` (`D3.active` is 1,
`D3.onFrame` is 0), so the standalone demo pays nothing for a hook nobody
listens to.

### Colour and width are global too

`Pen.setPenSize` and `Pen.setPenColor` are not per-sprite either: the last
caller wins for everyone. The renderer leaves the pen at `D3.penSize` (2) and
the backdrop pass leaves 30, so an overlay that redraws through
`D3.afterFrame` inherits whatever the 3D pass happened to end on.

The rule is therefore the same as for `eraseAll()`: **the last writer before
your strokes must be you.**

```jsw
public void Graph.render() {
    Pen.penUp();
    doState();              // drawNumber claims 1 for itself
    Pen.setPenSize(1);      // do not let the read-out's width leak into the plot
    doGraph();
}
```

Measured in the combined build, every `NpGraph` stroke now runs at diameter 1
and the overlay never sees 2 or 30. The read-out is a **5 × 8 bitmap whose
columns are 1 px apart**, so its pen must be 1 as well — at 2 or 3 the
neighbouring columns of a glyph merge and the digits turn into blobs. (The
original project only looked fat because it silently inherited
`Piano.draw()`'s 3.) Watch this with the pen-call recorder in
`tools/probe_pen.js`: it reports `penAttributes.diameter` per target, which is
the only way to see the leak — a screenshot just shows the wrong line width.

Two traps in that area, both hit while fixing the graph read-out:

* **`penDown` already draws a dot.** `scratch3_pen._penDown` calls
  `renderer.penPoint(…)` at the sprite's position every time, so
  `setXY(x, y); penDown(); penUp();` is a complete one-pixel dot. Adding a
  `setXY(x+1, y)` between them does not "make sure it inks" — it doubles the
  dot, and on the read-out's 6 px character grid the glyphs fuse into a smear
  that reads as the wrong font size. This pen stamps on *every* `penDown`, not
  only on the up→down transition.
* a list that only has monitor metadata — `// monitor Stage.Graph.nfont list at
  10,0` — is *declared but empty*. `indexOf()` then returns 0 and every lookup
  misses, so the drawing silently produces nothing. That, not the dot, was what
  hid the numbers. The glyph tables must be real initialisers:
  `public Graph.nfont = [...];`.

## Reviewing one shot

Two knobs, both settable from the host before the green flag as well as live
from the console.

`D3.holdTick` pins the show on a single tick, for a still:

```js
const V = (n) => Object.values(vm.runtime.getTargetForStage().variables).find(v => v[0] === n);
V('D3.holdTick')[1] = 22;   // shot 1 `cursor`, mid-typing
V('D3.holdTick')[1] = -1;   // release
```

The beat keeps running off the real clock while the show is pinned, so a held
frame still blinks on the beat.

`D3.startTick` and `D3.stopTick` play a **window** of the show instead. Set
`startTick` before the green flag and the clock re-origins so that tick is frame
one; `stopTick` freezes the frame it reaches, and later ticks draw nothing, so
the last pose stays on the stage rather than running on into the next shot:

```js
V('D3.startTick')[1] = 27;   // shot 2 `trace` starts here
V('D3.stopTick')[1] = 44;    // hold the frame at 44 and stop drawing
```

Because the beat clock is the song clock rather than the show position, a
windowed review still blinks and pulses in time with the music.

The film's first three shots are a **black frame**. The renderer paints it with
fourteen thick pen strokes rather than with a costume or a backdrop: the pen
layer is above every sprite, so the frame cannot hide the host's own drawing and
nothing has to be restored afterwards. It is painted inside `render()`, under
every shot, because the film is dark throughout.

## Reference frames from the film itself

Screenshots of our own output are not a target — that is how a projection four
times too large survived several rounds of review. The film is in
[`wem_example/`](../wem_example) and it runs locally, so the target can be the
real frames.

```sh
python3 tools/serve_cors.py 8766          # the repo, so /wem_example/ is reachable
# then open http://localhost:8766/wem_example/index.html
```

Two things about that page:

* **`window.player` only exists on localhost.** `main.js` gates it on the
  hostname, which is what makes the next step possible.
* **The song cannot load.** `assets/song_48k.wav` is fetched from a signed key
  service with `POST`, so it answers 501 and the page ends on
  `加载失败：音频 501`. The *visuals* are unaffected: the shaders compile for all
  fifteen chapters (a few minutes, watch the progress line) and
  `player.draw(t)` renders any moment.

```js
player.draw(8.0);                      // time in seconds
player.canvas                          // 3840 x 2160 backing store
```

`t` is seconds; our clock is ticks at 34.6664 a second, so `tick = t * 34.6664`.
Frames captured this way live in `build/film/`, with `ours_tN.png` beside them
for the same tick.

### What the comparison already showed

| tick | the film | `anim3d` |
| --- | --- | --- |
| 69 (t=2) | copper power line in perspective, a white box running along it, a via-dot field, bloom, `pulses 44 / v 1.70 U/S` | a flat black frame with three lines of text |
| 277 (t=8) | full-screen terminal: line-number gutter, seven lines of code with **per-word colour**, a glowing block cursor, a three-line `>` console, HUD at both corners, bracket-framed border | a small purple arc in the middle |
| 1040 (t=30) | a 262,144-point wireframe lattice, star field, bloom | a row of bars |

Two facts follow, and they are the whole design problem:

1. **The shot list does not match the film.** At the tick where the film types
   `object me {`, `anim3d` draws a dial. `gen_anim3d_show.py` chose motifs by
   keyword from chapter names; the film's shots are specific and hand-authored.
2. **The density is out of budget.** That shot draws 262,144 points. A pen at
   60 FPS manages on the order of 1,000–2,000 segments. No amount of renderer
   polish closes a thousandfold gap.

So the work is not "more detail everywhere". It is: pick shots we can actually
afford, and make those exact. The terminal shots are text, a cursor and a frame
— a pen can draw those almost perfectly, and they are a large share of the film.

## Verifying a frame without trusting a screenshot

Headless Chromium screenshots of a WebGL stage frequently come back **white**
even when the picture is correct: the drawing buffer is not preserved, so the
readback sees a cleared buffer. A white screenshot is not evidence of a bug — a
hand-drawn `pen_penDown` line comes out blank too.

Read the pen layer instead. In the TurboWarp console:

```js
const rt = vm.runtime;
rt.redrawRequested = true;
rt._step();
const { imageData, width, height } =
    rt.renderer.extractDrawableScreenSpace(rt.ext_pen._penDrawableId);
// imageData.data is bottom-up RGBA; sample alpha to see what was stroked
```

`extractDrawableScreenSpace` reads pixels in the same frame it draws, so it sees
the real picture. Drive one tick at a time to inspect a specific shot:

```js
const V = (n) => Object.values(vm.runtime.getTargetForStage().variables).find(v => v[0] === n);
V('Tick.tick')[1] = 240;      // scene 3, mid-shot
```

## Files

| path | what |
| --- | --- |
| `anim3d/core.jsw` | renderer, motifs, generated running order, test clock |
| `tools/gen_anim3d_show.py` | reads the film's chapters and clock, emits the data block |
| `examples/anim3d_host.jsw` | a stand-in NP4.26 that owns `Tick.tick`; the embedding fixture |
| `tools/embed_anim3d.py` | splice / refresh / verify the copy inside another project |
| `build/preview/anim3d.sb3` | `make anim3d` output, TurboWarp preview build |
| `build/preview/anim3d_host.sb3` | `make anim3d-embed` output, the embedded build |
