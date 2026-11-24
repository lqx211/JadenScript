# GTM — Teachable Machine models from a share link

`gtm` is a TurboWarp extension whose blocks take the share link Teachable
Machine gives you and nothing else:

```
https://teachablemachine.withgoogle.com/models/AbCdEf123_/
```

No export, no zip, no model files in the project. The link is enough because it
is a directory, and the three files a model needs sit in it:

| file | what it is |
| --- | --- |
| `model.json` | a tfjs **layers-model** topology; it names its own weights |
| `model.weights.bin` | the weights, resolved by `model.json` relative to itself |
| `metadata.json` | `labels`, `imageSize`, `grayscale`, `modelName`, `tfjsVersion` |

All three are served with `access-control-allow-origin: *`, on both the share
host (which 302s to a bucket) and the bucket. That is checked, not assumed:

```bash
curl -sSL -D - -o /dev/null \
  https://teachablemachine.withgoogle.com/models/AbCdEf123_/model.json \
  | grep -i access-control
# access-control-allow-origin: *
```

So the extension needs no Google SDK. The SDK's entire job is
`tf.loadLayersModel(model.json)` plus reading `metadata.json`.

## The two things that fail silently

Both are in `teachablemachine-community/libraries/image/src/`:

* **the input range is `[-1, 1]`**: `pixels.toFloat().div(127).sub(1)` — the
  divisor is 127, not 255 and not 127.5 (`utils/tf.ts`, `capture`);
* **the image is cover-cropped, not stretched**: scale so the *shorter* side
  reaches `imageSize`, then take the middle square (`utils/canvas.ts`, `cropTo`).

Getting either wrong compiles, runs, and predicts nonsense with a straight face,
which is why they are in one place in `gtm.js` and not spread over the blocks.

## Blocks

| block | kind | notes |
| --- | --- | --- |
| `gtm.load(url)` | command | waits for the model; `status` reports progress |
| `gtm.unload()` | command | disposes the model and stops the camera |
| `gtm.status()` | reporter | `idle`, `loading 42%`, `ready` or `error` |
| `gtm.lastError()` | reporter | the message behind `error` |
| `gtm.modelName()` | reporter | from `metadata.json` |
| `gtm.labels()` | reporter | the labels, comma separated |
| `gtm.labelAt(n)` | reporter | 1-based, like a Scratch list |
| `gtm.startCamera()` | command | `getUserMedia`, rear camera if there is one |
| `gtm.stopCamera()` | command | stops every track and removes the element |
| `gtm.cameraActive()` | boolean | |
| `gtm.classify()` | reporter | top label from the current camera frame |
| `gtm.classifyImage(url)` | reporter | top label in an image, so this works with no camera |
| `gtm.confidence(label)` | reporter | 0–1; `0` when the label is unknown |
| `gtm.isLabel(label)` | boolean | `confidence(label) >= threshold` |
| `gtm.predictions()` | reporter | every label with its confidence, as JSON |
| `gtm.threshold()` | reporter | default `0.5` |
| `gtm.setThreshold(n)` | command | clamped to `0…1` |

Labels are compared case-insensitively and trimmed, so `cat` finds `Cat`.

## Pose projects, and what decides the branch

An **Image** project and a **Pose** project are not the same kind of model, and
the difference is not cosmetic:

| | Image | Pose | Audio |
| --- | --- | --- | --- |
| classifier input | `[1, 224, 224, 3]` pixels, scaled by 127 | `[1, 14739]` posenet heatmaps + offsets | `[1, N]` spectrogram |
| extra library | — | `@teachablemachine/pose@0.8.3` | not supported |
| extra download | — | posenet MobileNet ≈ 13 MB | — |

**The model topology decides, not `metadata.json`.** The metadata is a hint
(`"packageName": "@teachablemachine/pose"`, or `modelSettings.posenet` on older
exports), but an export can omit it, and the failure mode is confusing:

```
Error: Expected dense_Dense5_input to have 2 dimensions
       but got array with shape [1,224,224,3]
```

That is a rank-2 classifier being handed a picture. `gtm.load` now reads
`batch_input_shape` out of `model.json` before choosing:

* rank 4 → image;
* rank 2 whose width is a posenet output (4131 / 14739 / 55473, i.e. 9×9, 17×17
  or 33×33 grid × 51) → pose, even when the metadata says nothing;
* any other rank 2 → **Audio**, which this extension cannot feed, so it says so
  by name and width instead of letting TensorFlow.js complain about dimensions.

The pose runtime is Teachable Machine's own, because the letterbox resize, the
channel order and the heatmap/offset concatenation all have to match training —
re-implementing that from `baseModel.predict` is how you get a model that loads,
runs, and is quietly wrong.

There is **one** tfjs on the page: **1.3.1**, the version TM's own snippets use
and the last build that still exports `tf.fromPixels`, which the pose runtime
calls. Two copies is the case that breaks — a page that loaded a pose model and
then an image model left the second one dead with `t is not a function` — so the
pin is deliberately shared (a layers-model from any TM version loads on it).

Pose projects cost about 13 MB more at load, once. `gtm.classify`,
`gtm.classifyImage`, `gtm.confidence` and `gtm.isLabel` behave exactly as they
do for an image model — verified against a real `up/left/right/stop` model:
`stop` at 0.972 with the four probabilities summing to 1.000000, and an image
model loading and classifying correctly after it in the same page.

What is **not** exposed yet: the keypoints themselves. `estimatePose` returns
them, so `gtm.posePoint("leftWrist")` would be a small addition to the same
branch, but no block does it today.

## Running it

The extension must run **unsandboxed**: the camera is in the page and
TensorFlow.js wants a WebGL context, and neither exists inside the sandbox.
TurboWarp only loads a URL unsandboxed when it starts with
`https://extensions.turbowarp.org/` or `http://localhost:8000/`, which is why the
`extensionURLs` entry points at the project server:

```bash
make serve                       # tools/serve.py --port 8000, with CORS
```

then, in the TurboWarp editor:

```
https://turbowarp.org/editor?extension=http://localhost:8000/userspace/school/gtm.js
```

or *Load extension from URL* with that same URL. `127.0.0.1` does not count;
it has to be `localhost`, port `8000`, exactly. Loading the file by hand also
works — tick *Run extension without sandbox*.

TensorFlow.js is fetched from jsDelivr, pinned at `TF_VERSION` in `gtm.js`, in
the page (not in a worker), and `webgl` falls back to `cpu` if the context is
unavailable. A machine with no WebGL still classifies, slowly.

## Vanilla Scratch

It cannot load a custom extension at all, so `gtm.js` throws before registering
anything:

* no `Scratch` global → *"this is a TurboWarp extension"*;
* a sandboxed player → *"must run unsandboxed"*.

That is the loud failure. The quiet one is a `.sb3` whose `extensionURLs` names a
`gtm` the target cannot fetch, and the compiler is where that should be caught:
`--target scratch` with a `gtm.` call is refused with `E0080`
(`jswc/cli.py`), because a Scratch build can neither ship the extension nor
reach the camera.

## In JadenScript

The blocks resolve like any other extension namespace: the table is
`jswc/gtm_blocks.py` (which also carries the URL as `GTM_URL`), and the calls
are ordinary:

```jsw
class detector {
    public void whenFlagClicked() {   // hook: green flag
        gtm.load("https://teachablemachine.withgoogle.com/models/AbCdEf123_/");
        gtm.startCamera();
    }

    public void classify() {          // hook: every frame is too often
        if (gtm.isLabel("Cat")) {
            Looks.say("cat");
        }
    }
}
```

`gtm.` also autocompletes in the VS Code extension: `make vscode-data`
regenerates `vscode-jsw/data/jsw.json` from the same table.

### The `extensionURLs` entry

`.sb3` cannot hold JavaScript, so the project carries the extension's **URL** in
`extensionURLs`. The compiler fills it from `EXTENSION_URLS` in
`jswc/opcodes.py`, next to the two memory extensions:

```python
"gtm": "http://localhost:8000/userspace/school/gtm.js",
```

Two consequences worth knowing:

* a build that uses `gtm.*` now loads the extension itself, so opening the
  `.sb3` is enough — nothing to paste into the editor;
* the URL is resolved **when the project opens**, so `make serve` has to be
  running, and the path is relative to the repository root. Rename or move
  `userspace/school/gtm.js` and the constant has to follow (the test
  `tests/test_gtm.py::test_the_url_is_one_the_player_runs_unsandboxed` checks
  the prefix, not the file, so a move still has to be done in both places).

`"invalid extension gtm"` in TurboWarp means exactly this entry is missing: the
project names the extension in `extensions` but carries no URL for it.

## Sharing the project: the `.sb3` is not portable on its own

The compiled project names the extension by **URL** (`extensionURLs`), and that
URL is `http://localhost:8000/…`. On another computer `localhost` is *that*
computer, so unless the repository is there too, the URL resolves to nothing.

What TurboWarp does about it, measured rather than assumed:

| situation | result |
| --- | --- |
| URL unreachable (no server on that machine) | `could not be loaded, usually due to a network error`, and the **editor crashes** — "Oops! Something went wrong" |
| URL served from any other origin (a different port, GitHub Pages, jsDelivr) | loads **sandboxed**, `gtm` throws `must run unsandboxed`, editor crashes |
| `http://localhost:8000/…` with `make serve` running | works |

So the `.sb3` alone is a development artefact. Three ways to hand it to someone:

1. **`jswc build … --html`** — the route that works, and it is one flag:

   ```bash
   make serve                       # only while packaging: the extension is
   jswc build userspace/school/gtm_demo.jsw -o build/gtm_demo.sb3 --html
   #   html      build/gtm_demo.html (1.9 MB, self-contained)
   ```

   `jswc` runs TurboWarp's own packager (`@turbowarp/packager`, `npm install`
   once), which embeds the player, the assets and `gtm.js` into one HTML file.
   Verified in a folder containing nothing but that file: the project loads,
   `gtm` registers unsandboxed and TensorFlow.js runs.

2. **TurboWarp Packager** by hand, if you want its full option set (title
   screen, appearance, controls). Paste the extension URL into its Extensions
   setting with `make serve` running.

3. **Install the repository there** and run `make serve`, then open the `.sb3`.
   Correct, and the workflow to use while you are still editing.

4. **`extensions.turbowarp.org`** — submit `gtm.js` there and point
   `EXTENSION_URLS` at its https URL; the `.sb3` is then portable without
   packaging. That is a pull request against TurboWarp, not something you can
   self-host: no other https origin is trusted unsandboxed.

## Checking it

```bash
node tools/check_gtm.js                  # guards, share-link parser, arithmetic
python3 -m unittest tests.test_gtm       # the same, plus table <-> script parity
```

`tools/check_gtm.js` runs the script in a `vm` context with a Scratch-shaped
stub and a `<script>` that fails instead of fetching TensorFlow.js, so "the link
parsed" is observable without a network, a camera or a GPU.
