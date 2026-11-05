# The first three shots, element by element

Everything below is read out of `wem_example`'s own sources — the shot bodies in
`src/ch/01_intro.js`, their helpers, and the `/** … */` design notes the film
carries next to them. It is a work list: each element is a thing `anim3d` has to
be able to draw.

> **Read this with the reference frames open.** The first version of this file
> was written from comments and shot *names* without rendering the film, and it
> was wrong in places: it described the opening as a black frame with a console
> typing itself out. Shot 1 has no text at all. Anything here that has not been
> checked against `build/film/` is a hypothesis, not a specification.

### Shot 1 `cursor` — what the code actually says

`src/ch/intro/cursor.js` and the first entry of `01_intro.js`:

```js
var CUR = { cx: 960, cy: 540, w: 50.4, h: 8.4 };   // design px
var blinkOn = (T, t) => T.beatPhase(t) < .5 ? 1 : 0;
draw(ctx) {
  reset();
  drawCursor(ctx.text.scene, cursorQuad(), { alpha: blinkOn(ctx.T, t) });
  overlays(ctx, K, { frame: 0 });
  look(ctx, K, { ...CURSOR_LOOK, ca: 0, vignette: .55, grain: lerp(.02, .035, seg(t, K.s0, K.on)) });
}
```

So the whole shot is **one filled white rectangle** — 50.4 × 8.4 design px, i.e.
12.6 × 2.1 stage px at 0.25 — centred, lit for the first half of every beat,
painted on the *text* layer so it blooms (`glow: 18`, `bloom: 1.05`), over
vignette .55 and grain. `frame: 0` means no HUD frame. Nothing else is drawn.

`anim3d` matches that now: two pen strokes, a wide dim one under a bright core to
stand in for the bloom. The vignette and grain are post effects a pen cannot do.
See `build/film/t000.png` against `build/film/ours_cursor.png`.

Three shots open the film, and they are one continuous idea:

| # | shot | starts at | length | camera |
| --- | --- | --- | --- | --- |
| 1 | `cursor` | `intro` start, 0.00 s (tick 0) | 26 ticks (0.75 s) | none — 2D overlay only |
| 2 | `trace` | `keys(T).on`, tick 26 | 55 ticks (1.6 s) | orthographic, top-down, zooming out |
| 3 | `current` | `keys(T).B(3)`, tick 81 | 45 ticks (1.3 s) | perspective, eye-low, turning in |

The film's design canvas is **1920 × 1080**. A Scratch stage is 480 × 360, so one
design pixel is **0.25 stage pixels** — every size below is quoted in design px
and divided by four when it is drawn.

---

## Shot 1 — `cursor`

One frame of black with a text cursor blinking on it. The whole point is the
blink, and it is on the music.

| element | detail |
| --- | --- |
| **the cursor** | a *filled* white rectangle, centred at (960, 540), **50.4 × 8.4 px** — not an outline, a solid block |
| its glow | 18 px of `#cfe0ff` behind the fill, drawn twice (glow pass, then a clean fill on top) |
| its blink | **lit for the first half of every beat, dark for the second** — `beatPhase(t) < .5` |
| **console lines** | typed code running down the left of the frame, one line per lyric line, starting 0.2 s before the section; grey on black, 6 px glow |
| **the frame** | suppressed in this shot (`frame: 0`) |
| **grain** | film grain ramping **.02 → .035** across the shot |
| **vignette** | .55 |
| chromatic aberration | 0 — the film is monochrome until bar 19.75 |
| 3D content | none.

What is *not* on screen matters as much: no grid, no net, no read-out. This shot
is the empty page.

---

## Shot 2 — `trace`

The camera pulls back from the cursor and the circuit board is drawn under it,
hop by hop, on the sixteenth notes.

### Camera

* **Orthographic**, looking straight down, centred on the origin.
* Height runs `h0 → 4.1` with `inOutSine`, where
  `h0 = cursorWidth3D × 1080 / cursorWidth2D` — i.e. the 3D cursor is framed at
  exactly the size of the 2D block from shot 1, then the camera rises to reveal
  the board.
* Bottom-right label: `view  top · orthographic`.

### The 3D cursor

A small **box**, `w 0.16 × h 0.028 × d 0.028`, sitting on the floor at the
origin (`y = h/2`), pure white and unlit. It is the "first pixel" the power
trace leaves from. Brightness `level × 1.35 × lit`; `lit` is the beat blink until
`keys(T).on`, then a constant 1.

### The dot grid

The board's copper vias, as a field of dots on the floor plane.

| parameter | value |
| --- | --- |
| dot size | 0.011 world units, minimum 1.2 px on screen |
| spread | 0.92, morphing to `spread × 1.05 × power` |
| wave | 9.5 — a radial wave from the origin |
| brightness | 0.75 |
| sparkle | 0.12 — a few dots flicker |
| depth of field | focus 5, no aperture |

### The power ring

The moment the board switches on: **two** circles at `y = 0.003`.

* outer: radius `0.12 → 3.72` over 0.95 s with `outCubic`, **160 segments**,
  brightness `(1−k)^1.5`, width `1.5 + 2.5·f` — it fades and thins as it grows.
* inner: radius `0.82 × outer`, **120 segments**, grey at `0.45·f`, width 1.2.

It leads the grid's wave: the ring is always slightly ahead of the dots.

### The net — how a trace grows

This is the part that needs care. A lane is a polyline with a length; the shot
draws a **growing head**, not the whole lane.

```
x      = (t − start) / sixteenth
hops   = floor(x) + outExpo(clamp(frac(x) / .6))
drawn  = clamp((hops − lane.t0) × STEP, 0, lane.len)
```

* one hop per **sixteenth note**
* inside each sixteenth the head moves with **outExpo over the first 60 %**, then
  holds — that is the "tick-tick-tick" stutter you see on screen
* each lane has its own `t0` offset, so lanes start at different hops
* `STEP` is a fixed world length per hop

### What the net is drawn with

| element | detail |
| --- | --- |
| **lanes** | main lanes at full brightness, secondary at **0.7** |
| **growing tips** | a bright dot at the head of every unfinished lane — width **11** on main lanes, **7** on others, brightness ×2.2 |
| **finished lanes** | the head dot dims to 1.2× the lane colour, width 6 |
| **vias** | a **16-segment ring** plus a centre dot at each via, appearing once `hops ≥ via.t`, growing over 2 hops |
| **via flash** | `min(1, age/.5) × exp(−max(0, age−.5)/1.2)` — a flare that decays over about a second |
| **rings** | a second, slower growth (its own `ringStart → ringEnd` window) drawn at 0.95 brightness |
| **inner rings** | at `ring × len`, colour ×0.45, width 1.4 |

### Read-out

Right of frame at (1480, 150), three rows, grey on black:

```
net      VCC
hop      03
copper   1.284
```

`hop` is `floor(hops)` padded to two digits; `copper` is the **total drawn
length** of every lane plus the rings, to three decimals. Both are live.

---

## Shot 3 — `current`

The same board, now with current running through it, seen from a low
perspective camera that drifts in.

### Camera

* **Perspective, fov 34**, looking at (0.42, 0, 0.04).
* Eye runs (1.18, 0.34, 0.62) → (1.06, 0.34, 0.5) with `outQuad` — a slow push.
* **Depth of field**: focus plane at the world point (0.72, 0, 0.12), aperture
  0.018, max blur **22 px**, three samples. Out-of-focus tracery softens; the
  net stays sharp.

### What is on screen

| element | detail |
| --- | --- |
| the 3D cursor | as before, lit |
| dot grid | brightness **0.45**, dot size **0.006** (half of shot 2 — the camera is closer), same DOF |
| the net | `widthK 1.25` — every trace 25 % thicker |
| **current pulses** | the new thing: bright heads **running outwards along every drawn lane** |

### The pulses

```
for each lane i with drawn length d:
    phase = (t × 1.7 + i × 0.137) mod 0.26
    for s = phase; s < d; s += 0.26:
        draw a streak of length 0.07 ending at s
```

* spacing **0.26** world units, speed **1.7 units/s**, tail **0.07**
* each lane's phase is offset by `i × 0.137` so the lanes do not pulse together
* brightness `min(1, s/.15, (d−s)/.1)` — pulses fade in at the start of a lane
  and out at its growing end
* main lanes full, secondary ×0.6; the head dot is 1.25× the streak width and
  1.5× brighter
* the count of live pulses is reported in the read-out

### Read-out

```
pulses   12
v        1.70 u/s
```

Amber while the board is booting, grey after.

---

## Status

Implemented as motifs **16 `cursorb`**, **17 `trace`**, **18 `current`**, with
Linux.jsw's terminal font decoded into `D3.gfont` (95 ASCII glyphs, 8 × 15, see
`tools/decode_linux_font.py`) and a read-out pass that draws straight to the pen
after the scene, so the HUD does not move with the camera and is not erased.

| shot | what is on screen | cost |
| --- | --- | --- |
| `cursor` | the block, lit on the beat; two source lines **typing themselves out** at the left; `BOOT · <section>` | no world geometry, 941 px |
| `trace` | 18 via dots, 10 Manhattan lanes growing by hop, vias, power ring, `NET/HOP/COPPER` | 60 segments, 3,072 px |
| `current` | the same board in perspective plus travelling pulses, `PULSES / V` | 140 segments, 5,013 px |

The read-out is **not decoration**: `D3.sections` is the real 16-section table of
the song, so the top line always names the section the show is in, and `D3.code`
holds lines from this very file — the film types its own source on screen, and so
does this. `typed` at tick 20 reads `if (_now`, which is line 2 of `frame()`.

Still missing from the list above: the orthographic camera is a 90° perspective
(so there is a little convergence), the growth ease is `1 − e^(−4·frac)` rather
than `outExpo`, and per-element pen sizes are coarser than the film's.

**What this port cannot do without the film's own copy.** The shot ids, sizes,
easings and timings are all recoverable from the code; the *words* are not. The
comments in `wem_example` reference a `docs/REMAKE.md` (§4 A, §12.7, §12.9 …)
that is not in the repository, so every string the film prints — the console
lines, the read-out labels, the code it types — has to come from a transcript of
that document, not from guessing.

## What `anim3d` had to grow to draw these

1. **Text.** There is no way to draw `net VCC` or `hop 03` with pen strokes
   unless the renderer owns a font. → Linux.jsw's terminal bitmap, decoded once
   offline into 15 row masks per glyph, drawn as one polyline run per run of set
   pixels.
2. **A filled block** — the cursor is solid, not an outline.
3. **A top-down orthographic camera** (pitch 90°) with no yaw sway.
4. **Growing polylines with hop offsets**, not "draw the whole edge list".
5. **A second, slower growth channel** for the rings, and vias with their own
   trigger hop.
6. **Pulse trains** parameterised by world length along a lane — the lane has to
   be stored as a polyline with cumulative length, not just endpoints.
7. **Per-element pen size**, so tips, vias and traces differ in weight. Scratch
   allows `set pen size` between strokes; it is one extra block per weight class.

Items 1–4 and 6 are the load-bearing ones; the rest is tuning.
