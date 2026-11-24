#!/usr/bin/env node
/* Exercise the gtm extension the way a player would, without a browser.

   The interesting behaviour here is not the inference -- that needs a camera, a
   WebGL context and TensorFlow.js -- it is everything that happens *before* and
   *around* it: the two guards that keep the extension out of players that cannot
   run it, the share-link parser, and the confidence/threshold arithmetic. Those
   are pure JavaScript and can be checked in a `vm` context with a Scratch-shaped
   stub, which is what this does.

   Prints one `ok`/`FAIL` line per check and a `SUMMARY {json}` line the Python
   suite compares against `jswc/gtm_blocks.py`. Exit status is the number of
   failures.

   Usage: node tools/check_gtm.js
*/

'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SCRIPT = path.join(__dirname, '..', 'userspace', 'school', 'gtm.js');
const source = fs.readFileSync(SCRIPT, 'utf8');

let failures = 0;

function check(name, condition, detail) {
  if (condition) {
    console.log(`ok   ${name}`);
  } else {
    failures += 1;
    console.log(`FAIL ${name}${detail ? ` -- ${detail}` : ''}`);
  }
}

function makeScratch(unsandboxed) {
  const registered = [];
  const Scratch = {
    BlockType: { COMMAND: 'command', REPORTER: 'reporter', BOOLEAN: 'Boolean' },
    ArgumentType: { STRING: 'string', NUMBER: 'number' },
    extensions: {
      unsandboxed,
      register(extension) {
        registered.push(extension);
      },
    },
  };
  return { Scratch, registered };
}

/* A context with just enough DOM for the module body: two 2d contexts, and a
   <script> that fires onerror instead of fetching TensorFlow.js. That makes
   "the link parsed" observable -- it gets as far as trying to load tf.js. */
function makeContext(
  Scratch,
  {
    failScripts = true,
    metadata = { labels: ['a'], packageName: '@teachablemachine/image' },
    topology = { class_name: 'Sequential', config: { layers: [
      { class_name: 'InputLayer', config: { batch_input_shape: [null, 224, 224, 3] } },
    ] } },
  } = {}
) {
  const context = {
    Scratch,
    // metadata.json and model.json answered locally: the link-parsing checks
    // only need to reach the *library* step, which the failing <script> makes
    // observable, and the topology is what decides image vs pose vs audio.
    fetch: async (url) => ({
      ok: true,
      json: async () => (String(url).endsWith('metadata.json') ? metadata : { modelTopology: topology }),
    }),
    console,
    setTimeout,
    clearTimeout,
    Promise,
    JSON,
    Math,
    Number,
    String,
    Boolean,
    Array,
    Object,
    Error,
    navigator: {},
    document: {
      createElement(tag) {
        const element = { tagName: String(tag).toUpperCase(), style: {}, width: 0, height: 0 };
        if (tag === 'canvas') {
          element.getContext = () => ({
            drawImage() {},
            clearRect() {},
            setTransform() {},
            scale() {},
            translate() {},
          });
        }
        if (tag === 'script') {
          Object.defineProperty(element, 'src', {
            get() {
              return this._src || '';
            },
            set(value) {
              this._src = value;
              if (failScripts) setTimeout(() => this.onerror && this.onerror(), 0);
            },
          });
        }
        return element;
      },
      head: { appendChild() {} },
      body: { appendChild() {} },
    },
  };
  vm.createContext(context);
  return context;
}

function load(context) {
  vm.runInContext(source, context, { filename: SCRIPT });
}

async function main() {
  // -- the guards ---------------------------------------------------------
  let message = '';
  try {
    vm.runInNewContext(source, {});
  } catch (error) {
    message = String(error.message);
  }
  check('vanilla Scratch is refused', /TurboWarp/.test(message), message);

  message = '';
  try {
    const { Scratch } = makeScratch(false);
    load(makeContext(Scratch));
  } catch (error) {
    message = String(error.message);
  }
  check('a sandboxed player is refused', /unsandboxed/.test(message), message);

  // -- registration -------------------------------------------------------
  const { Scratch, registered } = makeScratch(true);
  const context = makeContext(Scratch);
  let loaded = true;
  try {
    load(context);
  } catch (error) {
    loaded = false;
    check('registers in an unsandboxed player', false, String(error.message));
  }
  if (loaded) {
    check('registers exactly one extension', registered.length === 1, `got ${registered.length}`);
  }
  const extension = registered[0];
  if (!extension) {
    console.log(`SUMMARY ${JSON.stringify({ id: null, opcodes: [] })}`);
    return failures;
  }
  const info = extension.getInfo();
  check('the extension id is gtm', info.id === 'gtm', info.id);
  const blocks = info.blocks || [];
  const opcodes = blocks.map((block) => block.opcode);
  check('every block opcode is unique', new Set(opcodes).size === opcodes.length);
  const blockTypes = new Set(blocks.map((block) => block.blockType));
  check(
    'block types are ones a player knows',
    [...blockTypes].every((type) => ['command', 'reporter', 'Boolean'].includes(type)),
    [...blockTypes].join(', ')
  );

  // -- the share-link parser ---------------------------------------------
  await extension.load({ URL: 'not a link at all' });
  check('a junk link is an error', extension.status() === 'error', extension.status());
  check(
    'the junk link names the share link',
    /share link/.test(extension.lastError()),
    extension.lastError()
  );

  await extension.load({ URL: '   ' });
  check('an empty link is an error', extension.status() === 'error', extension.status());
  check(
    'the empty link says what to paste',
    /paste/.test(extension.lastError()),
    extension.lastError()
  );

  // A bare model id has to get past the parser: the next failure is tf.js.
  await extension.load({ URL: 'AbCdEf123_' });
  check(
    'a bare model id is accepted',
    /TensorFlow\.js/.test(extension.lastError()),
    extension.lastError()
  );

  // Also with the file name on the end, which is what people copy from the
  // console or from the exported snippet.
  extension.error = '';
  await extension.load({ URL: 'https://teachablemachine.withgoogle.com/models/AbCdEf123_/model.json' });
  check(
    'a model.json URL is accepted',
    /TensorFlow\.js/.test(extension.lastError()),
    extension.lastError()
  );

  // A pose project must take a different branch: the pose runtime, on tfjs 1.x.
  const poseScratch = makeScratch(true);
  const poseContext = makeContext(poseScratch.Scratch, {
    metadata: { labels: ['up', 'down'], packageName: '@teachablemachine/pose' },
    topology: { class_name: 'Sequential', config: { layers: [
      { class_name: 'Dense', config: { batch_input_shape: [null, 14739] } },
    ] } },
  });
  load(poseContext);
  const poseExtension = poseScratch.registered[0];
  await poseExtension.load({ URL: 'AbCdEf123_' });
  check(
    'a pose model pins tfjs 1.x',
    /TensorFlow\.js 1\.3\.1/.test(poseExtension.lastError()),
    poseExtension.lastError()
  );

  // The regression behind "expected dense_Dense5_input to have 2 dimensions but
  // got array with shape [1,224,224,3]": a rank-2 pose classifier reached the
  // image branch because the metadata did not say "pose".
  const blindScratch = makeScratch(true);
  const blindContext = makeContext(blindScratch.Scratch, {
    metadata: { labels: ['up', 'down'], packageName: '@teachablemachine/image' },
    topology: { class_name: 'Sequential', config: { layers: [
      { class_name: 'Dense', config: { batch_input_shape: [null, 14739] } },
    ] } },
  });
  load(blindContext);
  const blindExtension = blindScratch.registered[0];
  await blindExtension.load({ URL: 'AbCdEf123_' });
  check(
    'the topology alone routes a pose model',
    /TensorFlow\.js 1\.3\.1/.test(blindExtension.lastError()),
    blindExtension.lastError()
  );

  // An Audio project is rank 2 as well, and cannot be fed at all: say so
  // instead of letting tfjs complain about dimensions.
  const audioScratch = makeScratch(true);
  const audioContext = makeContext(audioScratch.Scratch, {
    metadata: { labels: ['yes', 'no'], packageName: '@teachablemachine/audio' },
    topology: { class_name: 'Sequential', config: { layers: [
      { class_name: 'InputLayer', config: { batch_input_shape: [null, 232] } },
    ] } },
  });
  load(audioContext);
  const audioExtension = audioScratch.registered[0];
  await audioExtension.load({ URL: 'AbCdEf123_' });
  check(
    'an audio model is named, not shape-errored',
    /Audio project/.test(audioExtension.lastError()),
    audioExtension.lastError()
  );
  check(
    'the audio refusal reports the width',
    /232 numbers/.test(audioExtension.lastError()),
    audioExtension.lastError()
  );

  // And a plain image model still takes the 4.x branch.
  const imageScratch = makeScratch(true);
  const imageContext = makeContext(imageScratch.Scratch);
  load(imageContext);
  const imageExtension = imageScratch.registered[0];
  await imageExtension.load({ URL: 'AbCdEf123_' });
  check(
    'an image model uses the same single tfjs',
    /TensorFlow\.js 1\.3\.1/.test(imageExtension.lastError()),
    imageExtension.lastError()
  );

  // -- the prediction arithmetic -----------------------------------------
  extension.labelList = ['Cat', 'Dog'];
  extension.last = [
    { label: 'Cat', confidence: 0.75 },
    { label: 'Dog', confidence: 0.25 },
  ];
  extension.minConfidence = 0.5;
  check('confidence matches a label', extension.confidence({ LABEL: 'cat' }) === 0.75);
  check('confidence is 0 for an unknown label', extension.confidence({ LABEL: 'Fish' }) === 0);
  check('confidence is 0 for an empty label', extension.confidence({ LABEL: '' }) === 0);
  check('isLabel is true above the threshold', extension.isLabel({ LABEL: 'Cat' }) === true);
  check('isLabel is false below the threshold', extension.isLabel({ LABEL: 'Dog' }) === false);
  extension.setThreshold({ VALUE: 0.9 });
  check('raising the threshold flips isLabel', extension.isLabel({ LABEL: 'Cat' }) === false);
  extension.setThreshold({ VALUE: -3 });
  check('the threshold is clamped at 0', extension.threshold() === 0);
  extension.setThreshold({ VALUE: 7 });
  check('the threshold is clamped at 1', extension.threshold() === 1);
  extension.setThreshold({ VALUE: 'half' });
  check('a non-numeric threshold is refused', /not a number/.test(extension.lastError()));
  check('the threshold survives a bad value', extension.threshold() === 1);

  check('labelAt is 1-based', extension.labelAt({ INDEX: 2 }) === 'Dog');
  check('labelAt is empty past the end', extension.labelAt({ INDEX: 3 }) === '');
  check('labelAt is empty below 1', extension.labelAt({ INDEX: 0 }) === '');

  check('predictions is the last list as JSON', extension.predictions() === JSON.stringify(extension.last));

  // -- the background update loop -----------------------------------------
  // A reporter only runs when the project asks, so without this loop every
  // `confidence` answered with the value from the last click on
  // `top label from the camera`.
  const idle = extension.updateInterval();
  check('the update interval starts at 1 ms', idle === 0.001, String(idle));
  extension.setUpdateInterval({ SECONDS: 0.05 });
  check('the interval can be set', extension.updateInterval() === 0.05);
  extension.setUpdateInterval({ SECONDS: 0 });
  check('0 means no limit', extension.updateInterval() === 0);
  extension.setUpdateInterval({ SECONDS: -1 });
  check('a negative interval is refused', /not a number of seconds/.test(extension.lastError()));
  check('the interval survives a bad value', extension.updateInterval() === 0);
  extension.setUpdateInterval({ SECONDS: 'soon' });
  check('a non-numeric interval is refused', /not a number of seconds/.test(extension.lastError()));

  extension.updateEvery = 10;                 // long enough not to fire here
  extension.state = 'ready';
  extension.last = [];
  extension.video = { readyState: 4 };
  extension.stream = { active: true };
  extension.keepUpdating();
  check('a ready model with a camera arms the loop', extension.timer !== null);
  check('nothing is classified before the interval elapses', extension.last.length === 0);
  extension.stopUpdates();
  check('the loop can be stopped', extension.timer === null);
  extension.video = null;
  extension.stream = null;
  extension.state = 'idle';
  check('labels is a readable list', extension.labels() === 'Cat, Dog');
  check('status is a word before loading', extension.status() === 'error' || extension.status() === 'idle');

  // Unloading has to leave nothing behind.
  extension.unload();
  check('unload clears the state', extension.status() === 'idle' && extension.labels() === '');
  check('unload clears the predictions', extension.predictions() === '[]');
  check('the camera is off after unload', extension.cameraActive() === false);

  console.log(`SUMMARY ${JSON.stringify({ id: info.id, opcodes })}`);
  return failures;
}

main().then(
  (count) => {
    process.exitCode = count > 0 ? 1 : 0;
    if (count > 0) console.log(`\n${count} check(s) failed`);
  },
  (error) => {
    console.log(`FAIL harness threw -- ${error && error.stack ? error.stack : error}`);
    process.exitCode = 1;
  }
);
