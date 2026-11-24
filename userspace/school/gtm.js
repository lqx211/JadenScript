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

  /* Pinned on purpose: a model trained by Teachable Machine is a plain
     layers-model, and tfjs loads across versions, but only known versions get
     tested with the WebGL backend this runs on. Bump it deliberately. */
  const TF_VERSION = '4.22.0';
  const TF_URL = `https://cdn.jsdelivr.net/npm/@tensorflow/tfjs@${TF_VERSION}/dist/tf.min.js`;

  const SHARE = 'https://teachablemachine.withgoogle.com/models/';
  const FILE_NAMES = /(model\.json|model\.weights\.bin|metadata\.json)$/i;

  /* -- TensorFlow.js, fetched once ---------------------------------------- */

  let tfPromise = null;

  function loadTF() {
    if (tfPromise) return tfPromise;
    tfPromise = new Promise((resolve, reject) => {
      const tf = globalThis.tf;
      if (tf) return resolve(tf);
      const script = document.createElement('script');
      script.src = TF_URL;
      script.async = true;
      script.onload = () => {
        if (globalThis.tf) resolve(globalThis.tf);
        else reject(new Error('gtm: TensorFlow.js loaded but defined nothing'));
      };
      script.onerror = () =>
        reject(new Error(`gtm: could not fetch TensorFlow.js from ${TF_URL}`));
      document.head.appendChild(script);
    }).then(async (tf) => {
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
      this.metadata = null;
      this.state = 'idle';
      this.error = '';
      this.progress = 0;
      this.minConfidence = 0.5;
      this.labelList = [];
      this.last = [];
      this.stream = null;
      this.video = null;
      this.busy = false;
      this.loadedFrom = '';
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
      let base;
      try {
        base = shareBase(args.URL);
      } catch (error) {
        this.state = 'error';
        this.error = error.message;
        return '';
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
        const tf = await loadTF();
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
        const model = await tf.loadLayersModel(base + 'model.json', {
          onProgress: (fraction) => {
            this.progress = fraction;
          },
        });
        this.dispose();
        this.model = model;
        this.metadata = metadata;
        this.labelList = metadata.labels.slice();
        this.loadedFrom = base;
        this.state = 'ready';
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
      this.metadata = null;
      this.labelList = [];
      this.last = [];
    }

    unload() {
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
        const stream = await navigator.mediaDevices.getUserMedia({
          video: { facingMode: 'environment' },
          audio: false,
        });
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
      } catch (error) {
        this.error = error && error.message ? error.message : String(error);
        this.stopCamera();
      }
      return '';
    }

    stopCamera() {
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

    /* -- inference --------------------------------------------------------- */

    async run(source) {
      if (!this.model || !this.metadata) {
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
