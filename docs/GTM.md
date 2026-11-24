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
`extensionURLs`, filled from `EXTENSION_URLS` in `jswc/opcodes.py`. That is the
one line the compiler still needs:

```python
"gtm": "http://localhost:8000/userspace/school/gtm.js",
```

Until it is there a compiled project contains the `gtm_*` blocks but no URL for
them, so the player shows them as unknown. Loading the extension by URL in the
editor (above) is unaffected — that path does not go through the project file.

## Distributing it

* **TurboWarp Packager** — bundle the extension with the project; that is the
  route for a classroom, since students then open one HTML file.
* **`extensions.turbowarp.org`** — the only https origin TurboWarp trusts
  unsandboxed, and it takes a pull request.
* **localhost** — fine for development and for you; not for handing out.

A project that references `http://localhost:8000/...` opens fine on a machine
that is not running the server only in the sense that the blocks are dead: the
project loads, the model does not.

## Checking it

```bash
node tools/check_gtm.js                  # guards, share-link parser, arithmetic
python3 -m unittest tests.test_gtm       # the same, plus table <-> script parity
```

`tools/check_gtm.js` runs the script in a `vm` context with a Scratch-shaped
stub and a `<script>` that fails instead of fetching TensorFlow.js, so "the link
parsed" is observable without a network, a camera or a GPU.
