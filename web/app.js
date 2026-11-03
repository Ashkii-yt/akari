// Akari's body: loads the .vrm, animates it, and listens to the python side over a websocket.
import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";
import { VRMLoaderPlugin, VRMUtils } from "@pixiv/three-vrm";

const $ = (id) => document.getElementById(id);
const cfg = await (await fetch("/api/config")).json();
// these two are used by helpers that can run before the setup code below finishes
let ws;
let hideTimer = 0;

// knobs
const LOOK_HEAD_YAW = 0.5;       // how much her head follows the cursor
const LOOK_HEAD_PITCH = 0.25;
const EMOTION_HOLD_S = 6;        // a mood lingers this long after she stops talking
const BUBBLE_MIN_MS = 3500;
const AWAKE_AFTER_MOVE_MS = 1500; // keep full frame rate this long after anything happens

// figure out what kind of GPU we really got. A software renderer (llvmpipe/SwiftShader) is
// the usual reason this whole thing feels laggy, so we tell the terminal about it.
function probeGpu() {
  const c = document.createElement("canvas");
  const gl = c.getContext("webgl2") || c.getContext("webgl");
  if (!gl) return "none";
  const ext = gl.getExtension("WEBGL_debug_renderer_info");
  const name = ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : "unknown";
  gl.getExtension("WEBGL_lose_context")?.loseContext();
  return name;
}
const gpu = probeGpu();
const software = /swiftshader|llvmpipe|softpipe|software/i.test(gpu);

const canvas = $("c");
let renderer;
try {
  renderer = new THREE.WebGLRenderer({
    canvas, alpha: true, premultipliedAlpha: true,
    antialias: cfg.antialias && !software,
    powerPreference: "low-power",
  });
} catch (e) {
  showBubble("", "WebGL isn't working here, so I can't show up. Check the terminal.", 0, true);
  // the normal websocket isn't open yet, so use a throwaway one to tell python what happened
  const w = new WebSocket(`ws://${location.host}/ws`);
  w.onopen = () => w.send(JSON.stringify({ type: "gfx", renderer: gpu, software: true, ms: null }));
  throw e;
}
renderer.setPixelRatio(software ? 1 : cfg.pixelRatio);
renderer.setClearColor(0x000000, 0);
renderer.outputColorSpace = THREE.SRGBColorSpace;

const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(30, 1, 0.1, 30);
const sun = new THREE.DirectionalLight(0xffffff, Math.PI);
sun.position.set(0.6, 1, 1);
scene.add(sun, new THREE.AmbientLight(0xffffff, 0.35));

let frame = { center: 1, dist: 3 };
function resize() {
  renderer.setSize(window.innerWidth, window.innerHeight, false);
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
}
window.addEventListener("resize", resize);
resize();

let vrm = null;
let state = "idle";
let emoTarget = null, emoTimer = 0;
let t = 0, mouth = 0, blinkClock = 2, blinking = 0;
let busyUntil = 0;
const emoNow = { happy: 0, relaxed: 0, sad: 0, angry: 0, surprised: 0 };
const cursor = { x: 0, y: 0 }, look = { x: 0, y: 0 };
const bounce = { y: 0, v: 0 };
const armSign = { L: -1, R: 1 };
const lookTarget = new THREE.Object3D();
scene.add(lookTarget);

const poke = () => { busyUntil = performance.now() + AWAKE_AFTER_MOVE_MS; };

async function loadModel() {
  if (!cfg.vrm) {
    showBubble("", "Drop a .vrm file into the models/ folder and restart me!", 0, true);
    return;
  }
  const loader = new GLTFLoader();
  loader.register((p) => new VRMLoaderPlugin(p));
  let gltf;
  try {
    gltf = await loader.loadAsync(cfg.vrm);
  } catch (e) {
    console.error(e);
    showBubble("", "I couldn't load that model. Is it a valid .vrm?", 0, true);
    return;
  }
  vrm = gltf.userData.vrm;
  VRMUtils.removeUnnecessaryVertices?.(gltf.scene);
  VRMUtils.combineSkeletons?.(gltf.scene);
  VRMUtils.rotateVRM0?.(vrm);
  vrm.scene.traverse((o) => { o.frustumCulled = false; });
  vrm.scene.scale.setScalar(cfg.scale || 1);
  scene.add(vrm.scene);
  if (vrm.lookAt) vrm.lookAt.target = lookTarget;
  lowerArms();
  fitCamera();
}

const bone = (name) => vrm?.humanoid?.getNormalizedBoneNode(name);

// Models come in a T-pose. Which way is "down" for the arms depends on the rig, so just try it.
function lowerArms() {
  const angle = cfg.armAngle;
  const goesDown = (upper, hand, sign) => {
    if (!upper || !hand) return true;
    upper.rotation.z = sign * angle;
    vrm.scene.updateMatrixWorld(true);
    const a = new THREE.Vector3(), b = new THREE.Vector3();
    upper.getWorldPosition(a);
    hand.getWorldPosition(b);
    return b.y < a.y;
  };
  armSign.L = goesDown(bone("leftUpperArm"), bone("leftHand"), -1) ? -1 : 1;
  armSign.R = goesDown(bone("rightUpperArm"), bone("rightHand"), 1) ? 1 : -1;
}

function fitCamera() {
  const box = new THREE.Box3().setFromObject(vrm.scene);
  const height = Math.max(0.5, box.max.y - box.min.y);
  const bust = cfg.framing === "bust";
  const visible = bust ? height * 0.62 : height * 1.12;
  const center = box.min.y + height * (bust ? 0.72 : 0.5);
  const dist = visible / 2 / Math.tan((camera.fov * Math.PI) / 360);
  frame = { center, dist };
  camera.position.set(0, center, dist);
  camera.lookAt(0, center, 0);
}

const lerp = (a, b, k) => a + (b - a) * Math.min(1, k);

function animate(dt) {
  t += dt;
  if (!vrm) return;

  look.x = lerp(look.x, cursor.x, dt * 4);
  look.y = lerp(look.y, cursor.y, dt * 4);
  lookTarget.position.set(look.x * 1.4, frame.center + 0.25 - look.y * 0.9, 2);

  const head = bone("head"), neck = bone("neck"), spine = bone("spine");
  const chest = bone("chest"), hips = bone("hips");
  const thinking = state === "thinking";
  if (head) {
    head.rotation.y = lerp(head.rotation.y, look.x * LOOK_HEAD_YAW + Math.sin(t * 0.5) * 0.03, dt * 8);
    head.rotation.x = lerp(head.rotation.x, look.y * LOOK_HEAD_PITCH + (thinking ? -0.08 : 0), dt * 8);
    head.rotation.z = lerp(head.rotation.z, thinking ? 0.1 : Math.sin(t * 0.37) * 0.02, dt * 5);
  }
  if (neck) neck.rotation.y = look.x * 0.15;
  if (chest) chest.rotation.x = Math.sin(t * 1.6) * 0.012;
  if (spine) spine.rotation.z = Math.sin(t * 0.6) * 0.02;
  if (hips) hips.rotation.y = Math.sin(t * 0.4) * 0.03;

  const sway = Math.sin(t * 1.3) * 0.02;
  const leftArm = bone("leftUpperArm"), rightArm = bone("rightUpperArm");
  if (leftArm) leftArm.rotation.z = armSign.L * cfg.armAngle + sway * armSign.L;
  if (rightArm) rightArm.rotation.z = armSign.R * cfg.armAngle - sway * armSign.R;

  // little hop when she perks up (a damped spring)
  bounce.y += bounce.v * dt;
  bounce.v += (-bounce.y * 120 - bounce.v * 8) * dt;
  vrm.scene.position.y = bounce.y;

  const face = vrm.expressionManager;
  if (!face) return;
  if (state === "idle" && emoTarget && (emoTimer -= dt) <= 0) emoTarget = null;
  const mood = emoTarget ?? (state === "listening" ? "surprised" : null);
  const strength = emoTarget ? (emoTarget === "surprised" ? 0.7 : 0.85) : 0.35;
  const speaking = state === "speaking";
  for (const name of Object.keys(emoNow)) {
    emoNow[name] = lerp(emoNow[name], name === mood ? strength : 0, dt * 6);
    face.setValue(name, emoNow[name] * (speaking ? 0.75 : 1));
  }

  blinkClock -= dt;
  if (blinkClock <= 0 && blinking === 0) {
    blinking = 0.0001;
    blinkClock = 2 + Math.random() * 4;
  }
  let lid = 0;
  if (blinking > 0) {
    blinking += dt / 0.15;
    lid = blinking < 0.5 ? blinking * 2 : Math.max(0, (1 - blinking) * 2);
    if (blinking >= 1) blinking = 0;
  }
  face.setValue("blink", lid);

  // the mouth just flaps while she talks, it isn't real lip-sync
  const open = speaking ? Math.max(0, 0.35 + Math.sin(t * 13) * 0.3 + Math.sin(t * 7.7 + 1) * 0.25) : 0;
  mouth = lerp(mouth, open, dt * 18);
  face.setValue("aa", mouth * 0.8);
  face.setValue("oh", speaking ? Math.max(0, Math.sin(t * 5.1)) * 0.25 : 0);
}

// Render loop. Full speed only while something is going on, otherwise a slow trickle,
// and nothing at all if the window is hidden.
const activeFps = software ? Math.min(cfg.fps, 15) : cfg.fps;
const bouncing = () => Math.abs(bounce.y) > 0.001 || Math.abs(bounce.v) > 0.01;
const isBusy = () => state !== "idle" || performance.now() < busyUntil || bouncing();

const cost = { total: 0, frames: 0, sent: false };
let last = performance.now();

function tick() {
  const now = performance.now();
  const dt = Math.min((now - last) / 1000, 0.1);
  last = now;

  if (!document.hidden) {
    animate(dt);
    vrm?.update(dt);
    renderer.render(scene, camera);
    if (!cost.sent && isBusy()) {
      cost.total += performance.now() - now;
      if (++cost.frames >= 90) reportGfx();
    }
  }
  const fps = isBusy() ? activeFps : cfg.idleFps;
  setTimeout(() => requestAnimationFrame(tick), 1000 / fps);
}

function reportGfx() {
  if (!ws || ws.readyState !== 1) return;
  cost.sent = true;
  send({ type: "gfx", renderer: gpu, software, ms: cost.total / cost.frames });
}

// speech bubble
function showBubble(you, her, holdMs, sticky = false) {
  $("you").textContent = you ? `“${you}”` : "";
  $("her").textContent = her || "";
  $("bubble").classList.add("show");
  clearTimeout(hideTimer);
  if (!sticky && holdMs !== undefined) scheduleHide(holdMs);
}
function scheduleHide(ms) {
  clearTimeout(hideTimer);
  hideTimer = setTimeout(() => $("bubble").classList.remove("show"), ms);
}

function setState(next) {
  const prev = state;
  state = next;
  poke();
  const chip = $("status");
  chip.textContent = { listening: "listening…", thinking: "thinking…" }[next] || "";
  chip.classList.toggle("show", !!chip.textContent);
  if (next === "listening" && prev !== "listening") bounce.v = 1.3;
  if (next === "idle") {
    emoTimer = EMOTION_HOLD_S;
    scheduleHide(Math.max(BUBBLE_MIN_MS, $("her").textContent.length * 40));
  } else if (next !== "speaking") {
    clearTimeout(hideTimer);
  }
}

// websocket to the python side
function send(obj) {
  if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj));
}
function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.type === "state") setState(m.state);
    else if (m.type === "emotion") {
      emoTarget = m.name === "neutral" ? null : m.name;
      emoTimer = EMOTION_HOLD_S;
      poke();
    } else if (m.type === "cursor") {
      cursor.x = m.x;
      cursor.y = m.y;
      poke();
    } else if (m.type === "notice") {
      showBubble("", m.text, 6000);
    } else if (m.type === "subtitle") {
      if (m.who === "you") {
        $("you").textContent = `“${m.text}”`;
        $("her").textContent = "";
      } else {
        $("her").textContent = m.append ? `${$("her").textContent} ${m.text}` : m.text;
        clearTimeout(hideTimer);
      }
      $("bubble").classList.add("show");
    }
  };
  ws.onclose = () => setTimeout(connect, 1500);
}
connect();

// mouse: drag to move her, click to poke, double-click to talk, right-click for the menu
let down = null;
canvas.addEventListener("pointerdown", (e) => {
  if (e.button !== 0) return;
  canvas.setPointerCapture(e.pointerId);
  canvas.style.cursor = "grabbing";
  down = { x: e.screenX, y: e.screenY, moved: false };
  send({ type: "drag", phase: "start" });
});
canvas.addEventListener("pointermove", (e) => {
  if (!down) return;
  const dx = e.screenX - down.x, dy = e.screenY - down.y;
  if (Math.abs(dx) + Math.abs(dy) > 4) down.moved = true;
  if (down.moved) send({ type: "drag", phase: "move", dx, dy });
});
canvas.addEventListener("pointerup", () => {
  if (!down) return;
  const moved = down.moved;
  down = null;
  canvas.style.cursor = "grab";
  send({ type: "drag", phase: "end" });
  if (moved) return;
  if (state === "speaking") {
    send({ type: "stop" });
  } else {
    bounce.v = 1.0;
    emoTarget = "surprised";
    emoTimer = 1.2;
    poke();
  }
});
canvas.addEventListener("dblclick", () => send({ type: "talk" }));
canvas.addEventListener("contextmenu", (e) => {
  e.preventDefault();
  send({ type: "menu" });
});

await loadModel();
tick();
