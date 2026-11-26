/* gtm — Teachable Machine models from their share link, for TurboWarp only.

   TurboWarp loads this unsandboxed, which is what makes it possible at all: the
   camera lives in the page, and TensorFlow.js wants a WebGL context. Vanilla
   Scratch cannot load a custom extension, so the file throws before it registers
   anything rather than failing later in a way that looks like a project bug.

   Loading a model is three fetches -- the share host serves them with
   `access-control-allow-origin: *`, so they work from any origin:

     https://teachablemachine.withgoogle.com/models/<id>/model.json
     https://teachablemachine.withgoogle.com/models/<id>/model.weights.bin
     https://teachablemachine.withgoogle.com/models/<id>/metadata.json

   `model.json` names the weights relative to itself, so tfjs resolves the second
   one for us. `metadata.json` is where the labels, the input size and the
   `grayscale` flag come from; the Teachable Machine SDK does nothing else.

   The two things that are easy to get wrong, and that fail silently:

     * the model expects [0, 255] scaled by 127 and shifted by 1, i.e. [-1, 1] --
       dividing by 255 instead compiles, runs, and predicts rubbish;
     * the image is scaled so its shorter side covers the input and the middle
       square is taken, not stretched to fit.

   Both match teachablemachine-community's `libraries/image/src/utils/tf.ts`
   (`capture`) and `utils/canvas.ts` (`cropTo`). */
(function (Scratch) {
  'use strict';

  if (typeof Scratch === 'undefined' || !Scratch || !Scratch.extensions) {
    throw new Error(
      'gtm: this is a TurboWarp extension. Scratch cannot load custom extensions, ' +
        'so a project that uses it cannot run on scratch.mit.edu.'
    );
  }
  if (!Scratch.extensions.unsandboxed) {
    throw new Error(
      'gtm: must run unsandboxed (it needs the camera and WebGL). Serve it from ' +
        'http://localhost:8000/ or load the file with "run extension without sandbox" checked.'
    );
  }

  /* One tfjs for every project kind, and it is the version Teachable Machine's
     own snippets use: 1.3.1 is the last build that still exports
     `tf.fromPixels`, which its pose runtime calls, and a layers-model from any
     TM version loads on it. Two copies on one page is the case that breaks --
     the second `load()` leaves the first model's backend unusable
     ("t is not a function"), so there is deliberately only ever one. */
  const TF_VERSION = '1.3.1';

  /* Pose projects are a different animal: the model does not take pixels at all,
     it takes posenet's heatmap + offset tensors (14739 floats for a 17-keypoint
     model), so the extension cannot classify without the pose estimator. That
     estimator is Teachable Machine's own runtime -- the version the exported
     project uses -- rather than a hand-rolled `baseModel.predict`, because the
     letterbox resize and the channel order have to match training exactly.
     The pairing is the one Teachable Machine's own docs give -- pose@0.8.3 on
     tfjs@1.3.1, the last tfjs that still exports `tf.fromPixels` -- while an
     image project keeps 4.x. */
  const TM_POSE_VERSION = '0.8.3';
  const TM_POSE_PATH =
    `@teachablemachine/pose@${TM_POSE_VERSION}/dist/teachablemachine-pose.min.js`;

  /* Where the two libraries come from. A packaged page can be served by a host
     whose Content-Security-Policy allows nothing but its own origin, so a build
     that copies them next to the page sets `window.GTM_LIB_BASE` and they load
     from there instead. Read per call, not at module scope: the page sets the
     global before the extension runs, but nothing guarantees the order. */
  function cdnBase() {
    const local = typeof window !== 'undefined' && window.GTM_LIB_BASE;
    return String(local || 'https://cdn.jsdelivr.net/npm/').replace(/\/?$/, '/');
  }

  const SHARE = 'https://teachablemachine.withgoogle.com/models/';
  const FILE_NAMES = /(model\.json|model\.weights\.bin|metadata\.json)$/i;

  /* A pose project is marked in `metadata.json`; the extra shape check is for
     the older exports that only carry `modelSettings.posenet`. */
  function isPose(metadata) {
    if (!metadata) return false;
    if (metadata.packageName === '@teachablemachine/pose') return true;
    return Boolean(metadata.modelSettings && metadata.modelSettings.posenet);
  }

  /* What a project *is* comes from the model, not from the metadata: a pose
     classifier is a two-layer dense net over posenet's output, so its input is
     `[null, 14739]` and feeding it `[1, 224, 224, 3]` is the error
     "expected dense_Dense5_input to have 2 dimensions but got array with shape
     [1,224,224,3]". The metadata is a hint -- an export may predate the
     `packageName` field, or be an Audio project, which is also rank 2 and which
     this extension cannot feed at all. */
  const POSENET_WIDTHS = new Set([9 * 9 * 51, 17 * 17 * 51, 33 * 33 * 51]);

  function firstInputShape(topology) {
    if (!topology || typeof topology !== 'object') return null;
    const config = topology.config || {};
    const own = config.batch_input_shape || config.batchInputShape;
    if (Array.isArray(own)) return own;
    if (Array.isArray(config.layers)) {
      for (const layer of config.layers) {
        const found = firstInputShape(layer);
        if (found) return found;
      }
    }
    return null;
  }

  /* 'image' (rank 4), 'pose' (rank 2, posenet width), 'flat' (any other rank 2). */
  function modelKind(topology, metadata) {
    const shape = firstInputShape(topology);
    if (!shape) return isPose(metadata) ? 'pose' : 'image';
    if (shape.length === 4) return 'image';
    if (shape.length === 2 && POSENET_WIDTHS.has(Number(shape[1]))) return 'pose';
    if (shape.length === 2) return 'flat';
    return isPose(metadata) ? 'pose' : 'image';
  }

  async function fetchJson(url) {
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`gtm: ${url} answered ${response.status}`);
    }
    return response.json();
  }

  /* -- the two libraries, fetched once ------------------------------------ */

  function injectScript(src, globalName, what, force) {
    return new Promise((resolve, reject) => {
      const existing = globalThis[globalName];
      if (existing && !force) return resolve(existing);
      const script = document.createElement('script');
      script.src = src;
      script.async = true;
      script.onload = () => {
        if (globalThis[globalName]) resolve(globalThis[globalName]);
        else reject(new Error(`gtm: ${what} loaded but defined no ${globalName}`));
      };
      script.onerror = () => reject(new Error(`gtm: could not fetch ${what} from ${src}`));
      document.head.appendChild(script);
    });
  }

  /* The pose runtime fetches posenet's pretrained weights from Google by an
     absolute URL of its own, so a same-origin build cannot point at them with a
     URL base the way it does for tfjs. It copies them instead and publishes
     `window.GTM_MIRROR_MAP`; `fetch` is wrapped for exactly those prefixes and
     left alone for everything else. Installed when a model loads, not at module
     scope, because the page sets the global after the extension is registered. */
  function installMirrors() {
    const map = typeof window !== 'undefined' && window.GTM_MIRROR_MAP;
    if (installMirrors.installed || !map || typeof window.fetch !== 'function') return;
    installMirrors.installed = true;
    const rewrite = (url) => {
      const text = String(url);
      for (const from of Object.keys(map)) {
        if (text.startsWith(from)) return String(map[from]) + text.slice(from.length);
      }
      return text;
    };
    const original = window.fetch;
    window.fetch = function (input, init) {
      if (typeof input === 'string') return original.call(this, rewrite(input), init);
      if (input && typeof input.url === 'string') {
        const url = rewrite(input.url);
        if (url !== input.url) return original.call(this, url, init);
      }
      return original.call(this, input, init);
    };
  }

  let tfPromise = null;

  function loadTF() {
    if (tfPromise) return tfPromise;
    const url = `${cdnBase()}@tensorflow/tfjs@${TF_VERSION}/dist/tf.min.js`;
    tfPromise = injectScript(url, 'tf', `TensorFlow.js ${TF_VERSION}`).then(async (tf) => {
      try {
        await tf.setBackend('webgl');
      } catch (error) {
        await tf.setBackend('cpu');
      }
      await tf.ready();
      return tf;
    });
    tfPromise.catch(() => {
      tfPromise = null;
    });
    return tfPromise;
  }

  /* -- the share link ----------------------------------------------------- */

  /* Accepts what people actually paste: the bare id, the share URL with or
     without the trailing slash, or the URL of one of the three files. */
  function shareBase(raw) {
    let text = String(raw == null ? '' : raw).trim();
    if (!text) {
      throw new Error('gtm: paste a Teachable Machine share link (models/<id>/)');
    }
    text = text.replace(FILE_NAMES, '');
    text = text.replace(/[?#].*$/, '');
    const bare = /^[A-Za-z0-9_-]+$/.test(text);
    if (bare) return SHARE + text + '/';
    const match = text.match(/teachablemachine\.withgoogle\.com\/models\/([A-Za-z0-9_-]+)\/?$/);
    if (!match) {
      throw new Error(`gtm: ${text} is not a Teachable Machine share link (models/<id>/)`);
    }
    return SHARE + match[1] + '/';
  }

  /* -- pixels ------------------------------------------------------------- */

  const scaled = document.createElement('canvas');
  const scaledCtx = scaled.getContext('2d');
  const square = document.createElement('canvas');
  const squareCtx = square.getContext('2d');

  /* Scale to cover, then take the middle square: exactly `cropTo`. */
  function squareOf(source, size) {
    const width = source.videoWidth || source.naturalWidth || source.width;
    const height = source.videoHeight || source.naturalHeight || source.height;
    if (!width || !height) return null;
    const scale = size / Math.min(width, height);
    const scaledW = Math.ceil(width * scale);
    const scaledH = Math.ceil(height * scale);
    scaled.width = scaledW;
    scaled.height = scaledH;
    scaledCtx.drawImage(source, 0, 0, scaledW, scaledH);
    const sx = Math.round((scaledW - size) / 2);
    const sy = Math.round((scaledH - size) / 2);
    square.width = size;
    square.height = size;
    squareCtx.clearRect(0, 0, size, size);
    squareCtx.drawImage(scaled, sx, sy, size, size, 0, 0, size, size);
    return square;
  }

  /* The same luminance weights TM uses when a model was trained in one channel. */
  function grayscale(tf, input) {
    const weights = tf.tensor1d([0.2989, 0.587, 0.114]);
    return tf.sum(input.mul(weights), 2, true);
  }

  /* -- the extension ------------------------------------------------------ */

  class GTM {
    constructor() {
      this.model = null;
      this.poseModel = null;
      this.metadata = null;
      this.state = 'idle';
      this.error = '';
      this.progress = 0;
      this.minConfidence = 0.5;
      /* Seconds between background classifications. 0 means "no limit": the
         next one starts as soon as the previous one finishes. */
      this.updateEvery = 0.001;
      this.labelList = [];
      this.last = [];
      this.stream = null;
      this.video = null;
      this.busy = false;
      this.loadedFrom = '';
      this.timer = null;
    }

    getInfo() {
      return {
        id: 'gtm',
        name: 'GTM',
        color1: '#FFAB19',
        color2: '#EC9C13',
        color3: '#BE8A04',
        blocks: [
          {
            opcode: 'load',
            blockType: Scratch.BlockType.COMMAND,
            text: 'load model from share link [URL]',
            arguments: {
              URL: {
                type: Scratch.ArgumentType.STRING,
                defaultValue: 'https://teachablemachine.withgoogle.com/models/',
              },
            },
          },
          {
            opcode: 'unload',
            blockType: Scratch.BlockType.COMMAND,
            text: 'unload the model and stop the camera',
          },
          {
            opcode: 'status',
            blockType: Scratch.BlockType.REPORTER,
            text: 'status',
          },
          {
            opcode: 'lastError',
            blockType: Scratch.BlockType.REPORTER,
            text: 'last error',
          },
          {
            opcode: 'modelName',
            blockType: Scratch.BlockType.REPORTER,
            text: 'model name',
          },
          {
            opcode: 'labels',
            blockType: Scratch.BlockType.REPORTER,
            text: 'all labels',
          },
          {
            opcode: 'labelAt',
            blockType: Scratch.BlockType.REPORTER,
            text: 'label [INDEX]',
            arguments: {
              INDEX: { type: Scratch.ArgumentType.NUMBER, defaultValue: 1 },
            },
          },
          {
            opcode: 'startCamera',
            blockType: Scratch.BlockType.COMMAND,
            text: 'turn the camera on',
          },
          {
            opcode: 'stopCamera',
            blockType: Scratch.BlockType.COMMAND,
            text: 'turn the camera off',
          },
          {
            opcode: 'cameraActive',
            blockType: Scratch.BlockType.BOOLEAN,
            text: 'camera is on?',
          },
          {
            opcode: 'classify',
            blockType: Scratch.BlockType.REPORTER,
            text: 'top label from the camera',
          },
          {
            opcode: 'classifyImage',
            blockType: Scratch.BlockType.REPORTER,
            text: 'top label in image [IMAGE]',
            arguments: {
              IMAGE: { type: Scratch.ArgumentType.STRING, defaultValue: '' },
            },
          },
          {
            opcode: 'confidence',
            blockType: Scratch.BlockType.REPORTER,
            text: 'confidence of [LABEL]',
            arguments: {
              LABEL: { type: Scratch.ArgumentType.STRING, defaultValue: '' },
            },
          },
          {
            opcode: 'isLabel',
            blockType: Scratch.BlockType.BOOLEAN,
            text: 'is it [LABEL]?',
            arguments: {
              LABEL: { type: Scratch.ArgumentType.STRING, defaultValue: '' },
            },
          },
          {
            opcode: 'predictions',
            blockType: Scratch.BlockType.REPORTER,
            text: 'all predictions as JSON',
          },
          {
            opcode: 'updateInterval',
            blockType: Scratch.BlockType.REPORTER,
            text: 'seconds between updates',
          },
          {
            opcode: 'setUpdateInterval',
            blockType: Scratch.BlockType.COMMAND,
            text: 'update every [SECONDS] seconds, 0 = as fast as it can',
            arguments: {
              SECONDS: { type: Scratch.ArgumentType.NUMBER, defaultValue: 0.001 },
            },
          },
          {
            opcode: 'threshold',
            blockType: Scratch.BlockType.REPORTER,
            text: 'confidence needed',
          },
          {
            opcode: 'setThreshold',
            blockType: Scratch.BlockType.COMMAND,
            text: 'set the confidence needed to [VALUE]',
            arguments: {
              VALUE: { type: Scratch.ArgumentType.NUMBER, defaultValue: 0.5 },
            },
          },
        ],
      };
    }

    /* -- loading ---------------------------------------------------------- */

    async load(args) {
      installMirrors();
      let base;
      try {
        base = shareBase(args.URL);
      } catch (error) {
        this.state = 'error';
        this.error = error.message;
        return '';
      }
      /* A same-origin build copies the model next to the page and sets
         `window.GTM_MODEL_MIRROR`, so the three files never leave the host --
         which is what a host with `connect-src 'self'` requires. The source
         still says the share link, so the same project works in the editor. */
      const mirror = typeof window !== 'undefined' && window.GTM_MODEL_MIRROR;
      if (mirror) {
        const id = base.slice(SHARE.length).replace(/\/$/, '');
        base = String(mirror).replace(/\/?$/, '/') + id + '/';
      }
      if (this.busy) {
        this.error = 'gtm: a model is already loading';
        return '';
      }
      this.busy = true;
      this.state = 'loading';
      this.error = '';
      this.progress = 0;
      try {
        const response = await fetch(base + 'metadata.json');
        if (!response.ok) {
          throw new Error(
            `gtm: metadata.json answered ${response.status} -- is ${base} a shared model?`
          );
        }
        const metadata = await response.json();
        if (!metadata || !Array.isArray(metadata.labels) || !metadata.labels.length) {
          throw new Error('gtm: metadata.json has no labels; that is not a Teachable Machine model');
        }
        // The topology decides, not the metadata: a rank-2 classifier can never
        // be handed pixels, and saying so here is much clearer than tfjs's
        // "expected dense_Dense5_input to have 2 dimensions".
        const topology = await fetchJson(base + 'model.json');
        const kind = modelKind(topology.modelTopology, metadata);
        if (kind === 'flat') {
          const width = firstInputShape(topology.modelTopology).slice(-1)[0];
          throw new Error(
            `gtm: ${base} wants ${width} numbers per frame, not a picture -- that is an ` +
              'Audio project (or a pose model this extension cannot feed). Only Image ' +
              'and Pose models are supported.'
          );
        }
        if (kind === 'pose') {
          // Metadata first, then the libraries: which tf.js is correct depends
          // on the branch, and the pose one needs 1.x.
          const tf = await loadTF();
          // `tf.fromPixels` is what the pose runtime calls; if a future pin
          // drops it again, `tf.browser.fromPixels` is the same function.
          if (!tf.fromPixels && tf.browser && tf.browser.fromPixels) {
            tf.fromPixels = tf.browser.fromPixels.bind(tf.browser);
          }
          const tmPose = await injectScript(
            cdnBase() + TM_POSE_PATH, 'tmPose', 'the pose runtime'
          );
          this.progress = 1;
          // The pose library loads the classifier *and* posenet itself.
          const poseModel = await tmPose.load(base + 'model.json', base + 'metadata.json');
          this.dispose();
          this.poseModel = poseModel;
        } else {
          const tf = await loadTF();
          const model = await tf.loadLayersModel(base + 'model.json', {
            onProgress: (fraction) => {
              this.progress = fraction;
            },
          });
          this.dispose();
          this.model = model;
        }
        this.metadata = metadata;
        this.labelList = metadata.labels.slice();
        this.loadedFrom = base;
        this.state = 'ready';
        /* The camera may already be on: start classifying in the background
           now, so `confidence`/`isLabel`/`predictions` are never stale. */
        this.keepUpdating();
      } catch (error) {
        this.state = 'error';
        this.error = error && error.message ? error.message : String(error);
      } finally {
        this.busy = false;
      }
      return '';
    }

    dispose() {
      if (this.model) {
        this.model.dispose();
        this.model = null;
      }
      if (this.poseModel) {
        this.poseModel.dispose();
        this.poseModel = null;
      }
      this.metadata = null;
      this.labelList = [];
      this.last = [];
    }

    unload() {
      this.stopUpdates();
      this.stopCamera();
      this.dispose();
      this.state = 'idle';
      this.loadedFrom = '';
      return '';
    }

    /* -- state ------------------------------------------------------------ */

    status() {
      if (this.state === 'loading') {
        return `loading ${Math.round(this.progress * 100)}%`;
      }
      return this.state;
    }

    lastError() {
      return this.error;
    }

    modelName() {
      if (!this.metadata) return '';
      return this.metadata.modelName || '';
    }

    labels() {
      return this.labelList.join(', ');
    }

    labelAt(args) {
      const index = Math.round(Number(args.INDEX)) - 1;
      if (!Number.isFinite(index) || index < 0 || index >= this.labelList.length) return '';
      return this.labelList[index];
    }

    /* -- camera ----------------------------------------------------------- */

    async startCamera() {
      try {
        if (this.video && this.stream && this.stream.active) return '';
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
          throw new Error(
            'gtm: this browser has no camera API here; open the project over https or localhost'
          );
        }
        /* `facingMode` as a plain value is a *required* constraint in some
           builds, and a laptop has no rear camera: there it throws
           OverconstrainedError and the page looks like it has none at all.
           `ideal` degrades to whatever camera exists. */
        let stream;
        try {
          stream = await navigator.mediaDevices.getUserMedia({
            video: { facingMode: { ideal: 'environment' } },
            audio: false,
          });
        } catch (error) {
          if (!error || error.name !== 'OverconstrainedError') throw error;
          stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
        }
        const video = document.createElement('video');
        video.playsInline = true;
        video.muted = true;
        video.autoplay = true;
        video.srcObject = stream;
        /* Attached but invisible: a detached video still decodes in Chrome but
           can be throttled in Safari, and `display: none` is the case that stops
           frames on some builds. */
        video.style.cssText =
          'position:fixed;left:0;top:0;width:2px;height:2px;opacity:0.01;pointer-events:none;';
        document.body.appendChild(video);
        await video.play();
        this.stream = stream;
        this.video = video;
        this.error = '';
        this.keepUpdating();
      } catch (error) {
        /* The name is the part that says what to do: NotAllowedError is the
           refused permission, NotFoundError means no camera at all. */
        const name = error && error.name ? `${error.name}: ` : '';
        this.error = name + (error && error.message ? error.message : String(error));
        this.stopCamera();
      }
      return '';
    }

    stopCamera() {
      this.stopUpdates();
      if (this.stream) {
        for (const track of this.stream.getTracks()) track.stop();
        this.stream = null;
      }
      if (this.video) {
        this.video.srcObject = null;
        this.video.remove();
        this.video = null;
      }
      return '';
    }

    cameraActive() {
      return Boolean(this.stream && this.stream.active && this.video);
    }

    /* -- the background loop ----------------------------------------------
       A Scratch project reads a reporter when it runs, so a model that is only
       classified when `top label from the camera` is clicked answers every
       `confidence` with the value from that one click -- a project that samples
       in a loop sees nothing move. The loop below keeps `this.last` fresh, at
       `updateInterval` seconds apart (0 = start the next one immediately),
       while a model is ready and the camera is on. */
    keepUpdating() {
      if (this.timer !== null) return;
      if (this.state !== 'ready' || !this.cameraActive()) return;
      const wait = this.updateEvery > 0 ? this.updateEvery * 1000 : 0;
      this.timer = setTimeout(() => {
        this.timer = null;
        if (this.state !== 'ready' || !this.cameraActive()) return;
        /* `busy` covers load() and an in-flight classification, and a video
           with no frame yet would classify as "nothing to classify". */
        if (!this.busy && this.video && this.video.readyState >= 2) {
          this.run(this.video);
        }
        this.keepUpdating();
      }, wait);
    }

    stopUpdates() {
      if (this.timer !== null) {
        clearTimeout(this.timer);
        this.timer = null;
      }
    }

    /* -- inference --------------------------------------------------------- */

    async run(source) {
      if ((!this.model && !this.poseModel) || !this.metadata) {
        this.error = 'gtm: load a model first';
        return null;
      }
      if (!source) {
        this.error = 'gtm: nothing to classify -- is the camera on?';
        return null;
      }
      if (this.busy) return this.last;
      this.busy = true;
      try {
        const predictions = this.poseModel
          ? await this._runPose(source)
          : await this._runImage(source);
        if (!predictions) return null;
        predictions.sort((a, b) => b.confidence - a.confidence);
        this.last = predictions;
        this.error = '';
        return predictions;
      } catch (error) {
        this.error = error && error.message ? error.message : String(error);
        return null;
      } finally {
        this.busy = false;
      }
    }

    /* Pose projects classify posenet's own output, not pixels: the estimator
       resizes, pads and hands back heatmaps + offsets (14739 floats), and the
       classifier is a two-layer dense net over that. Passing the image itself
       would be a shape error, which is why the metadata decides the branch. */
    async _runPose(source) {
      const { posenetOutput } = await this.poseModel.estimatePose(source);
      const classes = await this.poseModel.predict(posenetOutput);
      return classes.map((entry) => ({
        label: entry.className,
        confidence: entry.probability,
      }));
    }

    async _runImage(source) {
      const tf = await loadTF();
      const size = this.metadata.imageSize || 224;
      const canvas = squareOf(source, size);
      if (!canvas) {
        this.error = 'gtm: the image has no pixels yet';
        return null;
      }
      const scaledUp = tf.tidy(() => {
        let input = tf.browser.fromPixels(canvas).toFloat();
        if (this.metadata.grayscale) input = grayscale(tf, input);
        return this.model.predict(input.div(127).sub(1).expandDims(0));
      });
      const values = await scaledUp.data();
      scaledUp.dispose();
      const predictions = [];
      for (let i = 0; i < values.length && i < this.labelList.length; i += 1) {
        predictions.push({ label: this.labelList[i], confidence: values[i] });
      }
      return predictions;
    }

    async classify() {
      const predictions = await this.run(this.video);
      return predictions && predictions.length ? predictions[0].label : '';
    }

    async classifyImage(args) {
      const url = String(args.IMAGE || '').trim();
      if (!url) {
        this.error = 'gtm: give the image a URL';
        return '';
      }
      let image;
      try {
        image = await loadImage(url);
      } catch (error) {
        this.error = error && error.message ? error.message : String(error);
        return '';
      }
      const predictions = await this.run(image);
      return predictions && predictions.length ? predictions[0].label : '';
    }

    confidence(args) {
      const wanted = String(args.LABEL || '').trim().toLowerCase();
      if (!wanted) return 0;
      for (const prediction of this.last) {
        if (String(prediction.label).trim().toLowerCase() === wanted) {
          return prediction.confidence;
        }
      }
      return 0;
    }

    isLabel(args) {
      return this.confidence(args) >= this.minConfidence;
    }

    predictions() {
      return JSON.stringify(this.last);
    }

    updateInterval() {
      return this.updateEvery;
    }

    setUpdateInterval(args) {
      const value = Number(args.SECONDS);
      if (!Number.isFinite(value) || value < 0) {
        this.error = `gtm: ${args.SECONDS} is not a number of seconds`;
        return '';
      }
      this.updateEvery = value;
      /* Take effect now: the pending timer was armed with the old interval. */
      this.stopUpdates();
      this.keepUpdating();
      return '';
    }

    threshold() {
      return this.minConfidence;
    }

    setThreshold(args) {
      const value = Number(args.VALUE);
      if (!Number.isFinite(value)) {
        this.error = `gtm: ${args.VALUE} is not a number`;
        return '';
      }
      this.minConfidence = Math.min(1, Math.max(0, value));
      return '';
    }
  }

  function loadImage(url) {
    return new Promise((resolve, reject) => {
      const image = new Image();
      image.crossOrigin = 'anonymous';
      image.onload = () => resolve(image);
      image.onerror = () => reject(new Error(`gtm: could not load the image at ${url}`));
      image.src = url;
    });
  }

  Scratch.extensions.register(new GTM());
})(typeof Scratch !== 'undefined' ? Scratch : undefined);
