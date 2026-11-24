"""``gtm``: a Teachable Machine model from its share link, for TurboWarp only.

A Teachable Machine share link is a *directory*, not a file::

    https://teachablemachine.withgoogle.com/models/<id>/

and the three files a model needs sit beside it -- ``model.json`` (a tfjs
layers-model topology), ``model.weights.bin`` (the weights it names, which
``model.json`` resolves relative to itself) and ``metadata.json`` (the labels,
the input size and whether the model was trained on one channel). All three are
served with ``access-control-allow-origin: *``, from both the share host and the
bucket it redirects to, so a project running on any origin can fetch them. None
of this needs the Teachable Machine SDK: the SDK's whole job is
``tf.loadLayersModel`` plus reading ``metadata.json``.

Two things in the modelling are worth knowing because they are easy to get
wrong and the model does not complain, it just predicts nonsense:

* the input is ``pixels.toFloat().div(127).sub(1)`` -- the range is ``[-1, 1]``
  and the divisor is 127, not 255 and not 127.5;
* the image is scaled so its *shorter* side covers the model's input size and
  the middle square is taken, rather than being stretched to fit.

Both are what ``teachablemachine-community/libraries/image`` does in
``utils/tf.ts`` and ``utils/canvas.ts``.

This is a URL in ``extensionURLs`` (an .sb3 cannot hold JavaScript), and the
extension has to run unsandboxed: the camera and WebGL are both outside the
sandbox. TurboWarp only loads a URL unsandboxed when it starts with
``https://extensions.turbowarp.org/`` or ``http://localhost:8000/``, which is
why the URL below is the project server. Vanilla Scratch cannot load a custom
extension at all, so `jswc.cli` refuses a ``scratch`` build that uses these
blocks (E0080) rather than writing a project that only half works.
"""

from __future__ import annotations

#: Extension id; blocks are ``gtm_<opcode>`` in the project file.
GTM_EXTENSION = "gtm"

#: Where the player fetches the extension from. Port 8000 is not a preference:
#: it is one of the two prefixes TurboWarp trusts without a permission prompt.
GTM_URL = "http://localhost:8000/userspace/school/gtm.js"

#: extension id -> {namespace, blocks: {opcode: {type, args}}}
GTM_BLOCKS: dict[str, dict] = {
    GTM_EXTENSION: {
        "namespace": "gtm",
        "blocks": {
            # -- the model -------------------------------------------------
            "load": {"type": "COMMAND", "args": [("URL", "STRING", "")]},
            "unload": {"type": "COMMAND", "args": []},
            "status": {"type": "REPORTER", "args": []},
            "lastError": {"type": "REPORTER", "args": []},
            "modelName": {"type": "REPORTER", "args": []},
            "labels": {"type": "REPORTER", "args": []},
            "labelAt": {"type": "REPORTER", "args": [("INDEX", "NUMBER", "")]},
            # -- the camera ------------------------------------------------
            "startCamera": {"type": "COMMAND", "args": []},
            "stopCamera": {"type": "COMMAND", "args": []},
            "cameraActive": {"type": "BOOLEAN", "args": []},
            # -- classification --------------------------------------------
            "classify": {"type": "REPORTER", "args": []},
            "classifyImage": {"type": "REPORTER", "args": [("IMAGE", "STRING", "")]},
            "confidence": {"type": "REPORTER", "args": [("LABEL", "STRING", "")]},
            "isLabel": {"type": "BOOLEAN", "args": [("LABEL", "STRING", "")]},
            "predictions": {"type": "REPORTER", "args": []},
            "threshold": {"type": "REPORTER", "args": []},
            "setThreshold": {"type": "COMMAND", "args": [("VALUE", "NUMBER", "")]},
        },
    },
}
