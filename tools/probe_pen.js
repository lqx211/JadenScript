// Frame-exact pen probe for TurboWarp/Scratch projects.
//
// Load a project in the TurboWarp player, open devtools, then:
//   const s = document.createElement("script"); s.src = "<url>/probe_pen.js"; document.head.append(s);
//   __q5(320, 75)          // set camera rotation, run one frame, return every pen call
//
// It freezes the clock and steps the VM by hand, so two projects with the same
// blocks return byte-identical call lists. Compare the `calls` arrays of the
// original and the rebuild — that is the strongest possible check that the
// decompiler preserved the drawing.
window.__q5 = (rotZ, rotX) => {
  const vm = window.vm;
  const r = vm.runtime;
  const renderer = r.renderer;
  if (r._steppingInterval) { clearInterval(r._steppingInterval); r._steppingInterval = null; }
  r.currentStepTime = 1000 / 30;
  const RealDate = Date;
  window.__now = 1700000000000;
  window.Date = class extends RealDate {
    constructor(...args) { super(...(args.length ? args : [window.__now])); }
    static now() { return window.__now; }
  };
  performance.now = () => window.__now;
  r.ioDevices.clock._paused = true;
  r.ioDevices.clock.projectTimer = () => 0;
  r.ioDevices.clock.resetProjectTimer();
  const norm = (s) => String(s).replace(/[^A-Za-z0-9]/g, "").toLowerCase();
  const find = (name) => {
    const n = norm(name);
    for (const t of r.targets) for (const id of Object.keys(t.variables)) {
      const v = t.variables[id];
      if (norm(v.name) === n) return v;
    }
    return null;
  };
  const set = (name, value) => { const v = find(name); if (v) v.value = value; return !!v; };
  const log = [];
  for (const name of ["penPoint", "penLine", "penClear", "penStamp"]) {
    const original = renderer[name];
    renderer[name] = function (...args) {
      if (log.length < 5000) {
        const st = args[1] && args[1].color4f
          ? args[1].color4f.map((c) => Math.round(c * 1000)).join(",") + "@" + Math.round((args[1].diameter || 0) * 100)
          : "-";
        log.push(name + "|" + st + "|" + (Math.round(args[2] * 100) / 100) + "|" + (Math.round(args[3] * 100) / 100) + (args.length > 4 ? "|" + (Math.round(args[4] * 100) / 100) + "|" + (Math.round(args[5] * 100) / 100) : ""));
      }
      return original.apply(renderer, args);
    };
  }
  const step = (n) => { for (let i = 0; i < n; i++) { window.__now += 1000 / 30; r._step(); } renderer.draw(); };

  vm.stopAll();
  vm.greenFlag();
  step(20);
  set("viewed document", "");
  set("render mode", "points");
  step(3);
  const applied = {
    rotZ: set("cam rot z", rotZ),
    rotX: set("cam rot x", rotX),
    camX: set("cam x", -0.65),
    camY: set("cam y", 0.0734),
    camZ: set("cam z", 1.65),
  };
  step(3);
  log.length = 0;
  step(1);
  return {
    applied,
    actual: {
      rotZ: String(find("cam rot z").value),
      rotX: String(find("cam rot x").value),
    },
    total: log.length,
    calls: log,
  };
};
