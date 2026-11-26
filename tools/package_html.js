#!/usr/bin/env node
/* Compile-side companion to `jswc build --html`.

   The project arrives on stdin as a complete `.sb3` and leaves as one
   self-contained HTML file, because that is what the TurboWarp Packager does
   and none of it is worth re-implementing:

     * the player itself (scratch-vm, the renderer, the audio engine) is
       embedded, so the file runs with no network at all;
     * every asset is inlined;
     * each `extensionURLs` entry is fetched and rewritten to a
       `data:text/javascript,` URL, which is what makes a project that depends
       on `gtm` portable -- see docs/GTM.md.

   Protocol: one JSON object on stdout, always. `{ok: true, …}` on success,
   `{ok: false, error, message}` on failure, so the Python side can turn
   "the packager is not installed" into a sentence rather than a stack trace.

   Usage:
     node tools/package_html.js --out build/x.html [--title "My Project"] \\
       [--no-bake] [--no-turbo] [--custom-js "window.X = 1"] < x.sb3
*/

'use strict';

const fs = require('fs');

function parseArgs(argv) {
  const args = { out: null, title: null, bake: true, customJs: null, turbo: true };
  for (let i = 0; i < argv.length; i += 1) {
    const flag = argv[i];
    if (flag === '--out') args.out = argv[(i += 1)];
    else if (flag === '--title') args.title = argv[(i += 1)];
    else if (flag === '--custom-js') args.customJs = argv[(i += 1)];
    // Extensions stay the URLs the project already names, instead of being
    // inlined as `data:` scripts: a host whose script-src has no `data:` can
    // still load them, as long as they are same-origin files.
    else if (flag === '--no-bake') args.bake = false;
    // Turbo mode runs the VM flat out, which is right for a machine that is
    // meant to finish, and wrong for a game whose animation is timed by the
    // frame: it plays at whatever speed the CPU allows.
    else if (flag === '--no-turbo') args.turbo = false;
    else if (flag === '--help' || flag === '-h') args.help = true;
  }
  return args;
}

function emit(payload) {
  process.stdout.write(`${JSON.stringify(payload)}\n`);
}

const args = parseArgs(process.argv.slice(2));
if (args.help) {
  emit({ ok: true, usage: 'node tools/package_html.js --out FILE.html [--title TEXT] < project.sb3' });
  process.exit(0);
}
if (!args.out) {
  emit({ ok: false, error: 'usage', message: '--out is required' });
  process.exit(2);
}

let Packager;
try {
  Packager = require('@turbowarp/packager');
} catch (error) {
  // The exact version is pinned in package.json on purpose: the packager's
  // Node API changes across minor releases.
  emit({
    ok: false,
    error: 'missing-packager',
    message: String((error && error.message) || error),
  });
  process.exit(3);
}

const projectData = fs.readFileSync(0);

(async () => {
  const project = await Packager.loadProject(projectData);
  const packager = new Packager.Packager();
  packager.project = project;
  // Only the defaults that matter for a deliverable: no "click to start" gate,
  // and the same options the packager website uses for an HTML target.
  packager.options.target = 'html';
  packager.options.turbo = args.turbo;
  packager.options.autoplay = true;
  packager.options.bakeExtensions = args.bake;
  if (args.title) packager.options.app.windowTitle = args.title;
  if (args.customJs) packager.options.custom.js = args.customJs;
  const result = await packager.package();
  fs.writeFileSync(args.out, result.data);
  emit({ ok: true, type: result.type, bytes: result.data.length, filename: result.filename });
})().catch((error) => {
  emit({
    ok: false,
    error: 'package',
    message: String((error && error.stack) || error),
  });
  process.exit(1);
});
