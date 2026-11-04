const $ = id => document.getElementById(id);
const cam = $("cam"), photo = new Image(), overlay = $("overlay"), g = overlay.getContext("2d");
const orb = $("orb"), who = $("who"), text = $("text"), warn = $("warn"), start = $("start");
const small = document.createElement("canvas"), sg = small.getContext("2d", { willReadFrequently: true });
const big = document.createElement("canvas"), bg = big.getContext("2d");
small.width = small.height = 32;

const WORDS = {
  en: {
    greet: "Hi! What do you want help with today?",
    start: "Tap to start",
    mic: "Allow the microphone",
    cam: "Allow the camera",
    denied: "I can't hear or see yet. Tap the lock next to the web address, allow it, then tap here.",
    lost: "Connection lost. Tap to restart.",
  },
  hi: {
    greet: "नमस्ते! आज आपको किस चीज़ में मदद चाहिए?",
    start: "शुरू करने के लिए दबाएँ",
    mic: "माइक की अनुमति दें",
    cam: "कैमरे की अनुमति दें",
    denied: "मैं अभी सुन या देख नहीं पा रही। ऊपर पते के पास ताले पर दबाइए, अनुमति दीजिए, फिर यहाँ दबाइए।",
    lost: "कनेक्शन टूट गया। फिर शुरू करने के लिए दबाइए।",
  },
  mr: {
    greet: "नमस्कार! आज तुम्हाला कशात मदत हवी आहे?",
    start: "सुरू करण्यासाठी दाबा",
    mic: "माइकला परवानगी द्या",
    cam: "कॅमेऱ्याला परवानगी द्या",
    denied: "मला अजून ऐकू किंवा दिसत नाही. वर पत्त्याजवळच्या कुलुपावर दाबा, परवानगी द्या, मग इथे दाबा.",
    lost: "कनेक्शन तुटले. पुन्हा सुरू करण्यासाठी दाबा.",
  },
  or: {
    greet: "ନମସ୍କାର! ଆଜି ଆପଣଙ୍କୁ କେଉଁଥିରେ ସାହାଯ୍ୟ ଦରକାର?",
    start: "ଆରମ୍ଭ କରିବାକୁ ଦବାନ୍ତୁ",
    mic: "ମାଇକ୍‌କୁ ଅନୁମତି ଦିଅନ୍ତୁ",
    cam: "କ୍ୟାମେରାକୁ ଅନୁମତି ଦିଅନ୍ତୁ",
    denied: "ମୁଁ ଏବେ ଶୁଣି କିମ୍ବା ଦେଖି ପାରୁନାହିଁ। ଉପରେ ଠିକଣା ପାଖରେ ଥିବା ତାଲା ଦବାନ୍ତୁ, ଅନୁମତି ଦିଅନ୍ତୁ, ତାପରେ ଏଠି ଦବାନ୍ତୁ।",
    lost: "ସଂଯୋଗ ବିଚ୍ଛିନ୍ନ ହେଲା। ପୁଣି ଆରମ୍ଭ କରିବାକୁ ଦବାନ୍ତୁ।",
  },
};
const BASE = navigator.language.split("-")[0], LANG = WORDS[BASE] ? navigator.language : "en-US";
const W = WORDS[BASE] || WORDS.en;
const face = $("face"), label = $("label"), bubble = $("bubble");
const ICONS = {
  mic: '<svg viewBox="0 0 24 24"><rect x="9" y="3" width="6" height="11" rx="3" fill="#111"/><path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" fill="none" stroke="#111" stroke-width="2" stroke-linecap="round"/></svg>',
  cam: '<svg viewBox="0 0 24 24"><path d="M3 8h3.5l2-2.5h7l2 2.5H21v11H3z" fill="#111"/><circle cx="12" cy="13.2" r="3.6" fill="#fff"/></svg>',
  hello: '<svg viewBox="0 0 24 24"><path d="M4 9h4l5-4v14l-5-4H4z" fill="#111"/><path d="M16 9a4 4 0 0 1 0 6M18.5 6.5a7.5 7.5 0 0 1 0 11" fill="none" stroke="#111" stroke-width="2" stroke-linecap="round"/></svg>',
};
label.textContent = W.start;
const SAME_SCRIPT = { mr: "hi", ne: "hi" };
let ws, ctx, box = null, boxTimer, lost = false, muteUntil = 0, heard = false, greeting = false;
let prev = null, sent = null, still = 0, lastSend = 0;
let micStream, camStream;

start.onclick = async () => {
  if (micStream && camStream) return begin();
  ctx ||= new AudioContext({ sampleRate: 16000 });
  ctx.resume();
  try {
    if (!micStream) {
      mood("listening", W.mic, "mic");
      micStream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
      });
      hear(micStream);
      await pause(1200);
    }
    if (!camStream) {
      mood("looking", W.cam, "cam");
      camStream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "environment", width: { ideal: 1920 } },
      });
    }
  } catch {
    return mood("sad", W.denied, micStream ? "cam" : "mic");
  }
  cam.srcObject = camStream;
  mood("happy ready", W.start, "hello");
};

function begin() {
  say({ text: W.greet, lang: LANG, warning: false });
  ctx.resume();
  navigator.wakeLock?.request("screen").catch(() => {});
  connect();
  startMic(ctx, micStream);
  setInterval(watch, 500);
  start.remove();
  setTimeout(() => document.body.addEventListener("click", () => {
    if (lost) return location.reload();
    hush();
    hideBox();
  }));
}

function mood(name, words, icon) {
  start.className = name;
  label.textContent = words;
  if (icon) bubble.innerHTML = ICONS[icon];
  bubble.classList.toggle("shown", !!icon);
}

function pause(ms) {
  return new Promise(done => setTimeout(done, ms));
}

function hear(stream) {
  const meter = ctx.createAnalyser(), data = new Uint8Array(256);
  meter.fftSize = 256;
  ctx.createMediaStreamSource(stream).connect(meter);
  (function tick() {
    if (!start.isConnected) return;
    meter.getByteTimeDomainData(data);
    const peak = data.reduce((m, v) => Math.max(m, Math.abs(v - 128)), 0);
    face.style.setProperty("--level", Math.min(1, peak / 40).toFixed(2));
    requestAnimationFrame(tick);
  })();
}

function connect() {
  ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws");
  ws.onopen = () => {
    ws.send(JSON.stringify({ type: "voices", langs: [...new Set(speechSynthesis.getVoices().map(v => v.lang))] }));
  };
  ws.onmessage = e => {
    const m = JSON.parse(e.data);
    if (m.type === "say") say(m, true);
    else if (m.type === "caption") onCaption(m.text);
    else if (m.type === "state") orb.className = m.state;
    else if (m.type === "box") showBox(m);
  };
  ws.onclose = () => { lost = true; show("", W.lost, "idle"); };
}

function showBox(m) {
  clearTimeout(boxTimer);
  if (!m.box) return hideBox();
  box = m.box;
  photo.src = "data:image/jpeg;base64," + m.image;
  boxTimer = setTimeout(hideBox, 8000);
}

function hideBox() {
  box = null;
  overlay.style.opacity = 0;
}

function onCaption(msg) {
  if (speechSynthesis.speaking) hush();
  show("You", msg, "listening");
  if (!heard && cam.videoWidth) {
    heard = true;
    sent = grey();
    sendFrame(true);
  }
}

function show(name, msg, state) {
  who.textContent = name;
  text.textContent = msg;
  orb.className = state;
}

function talking() {
  return speechSynthesis.speaking && Date.now() < muteUntil;
}

function hush() {
  speechSynthesis.cancel();
  muteUntil = 0;
}

function say(m, timed) {
  const voice = pickVoice(m.lang);
  heard = false;
  greeting = !timed;
  warn.hidden = !m.warning;
  warn.textContent = m.warning ? m.text : "";
  show(voice ? "Guide" : "Guide 🔇", m.text, "speaking");
  if (!voice) {
    return speechSynthesis.cancel();
  }
  muteUntil = Date.now() + Math.min(15000, 1500 + m.text.length * 90);
  const u = new SpeechSynthesisUtterance(m.text);
  u.lang = m.lang;
  u.voice = voice;
  if (speechSynthesis.speaking) {
    speechSynthesis.cancel();
    setTimeout(() => speechSynthesis.speak(u), 100);
  } else {
    speechSynthesis.speak(u);
  }
}

const chosen = {};

function pickVoice(lang) {
  const voices = speechSynthesis.getVoices(), l = lang.toLowerCase(), base = l.split("-")[0];
  const norm = v => v.lang.toLowerCase().replace("_", "-");
  return chosen[base] ||= voices.find(v => norm(v) === l)
    || voices.find(v => norm(v).startsWith(base))
    || voices.find(v => norm(v).startsWith(SAME_SCRIPT[base]))
    || null;
}

function startMic(ctx, stream) {
  ctx.resume();
  const proc = ctx.createScriptProcessor(2048, 1, 1);
  proc.onaudioprocess = e => {
    if (ws.readyState !== 1) return;
    const pcm = toPCM16(e.inputBuffer.getChannelData(0), ctx.sampleRate);
    ws.send(greeting && talking() ? new ArrayBuffer(pcm.byteLength) : pcm);
  };
  ctx.createMediaStreamSource(stream).connect(proc);
  proc.connect(ctx.destination);
}

function toPCM16(f32, rate) {
  const ratio = rate / 16000, out = new Int16Array(Math.floor(f32.length / ratio));
  for (let i = 0; i < out.length; i++) out[i] = Math.max(-1, Math.min(1, f32[Math.floor(i * ratio)])) * 32767;
  return out.buffer;
}

function grey() {
  sg.drawImage(cam, 0, 0, 32, 32);
  const d = sg.getImageData(0, 0, 32, 32).data, out = new Uint8Array(1024);
  for (let i = 0; i < 1024; i++) out[i] = (d[i * 4] + d[i * 4 + 1] + d[i * 4 + 2]) / 3;
  return out;
}

function diff(a, b) {
  if (!a || !b) return 255;
  let s = 0;
  for (let i = 0; i < a.length; i++) s += Math.abs(a[i] - b[i]);
  return s / a.length;
}

function watch() {
  if (orb.className === "speaking" && !talking()) orb.className = "idle";
  if (!cam.videoWidth || ws.readyState !== 1) return;
  const now = grey();
  still = diff(now, prev) < 12 ? still + 1 : 0;
  prev = now;
  if (still >= 2 && diff(now, sent) > 14 && Date.now() - lastSend > 2500) { sent = now; sendFrame(false); }
}

function sendFrame(quiet) {
  lastSend = Date.now();
  const w = Math.min(1280, cam.videoWidth);
  big.width = w;
  big.height = Math.round(cam.videoHeight * w / cam.videoWidth);
  bg.drawImage(cam, 0, 0, big.width, big.height);
  ws.send(JSON.stringify({ type: "frame", quiet, image: big.toDataURL("image/jpeg", 0.8).split(",")[1] }));
}

function draw() {
  const dpr = devicePixelRatio || 1;
  overlay.width = innerWidth * dpr;
  overlay.height = innerHeight * dpr;
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  if (!box) return;
  const W = photo.naturalWidth, H = photo.naturalHeight;
  const x1 = box[0] * W, y1 = box[1] * H, x2 = box[2] * W, y2 = box[3] * H;
  const sw = Math.min(W, H / .75, Math.max((x2 - x1) * 4, W * .5)), sh = sw * .75;
  const sx = Math.max(0, Math.min(W - sw, (x1 + x2 - sw) / 2));
  const sy = Math.max(0, Math.min(H - sh, (y1 + y2 - sh) / 2));
  const cw = Math.min(innerWidth - 32, 360), ch = cw * .75, cx = (innerWidth - cw) / 2, cy = 56, k = cw / sw;
  const bx = cx + (x1 - sx) * k - 8, by = cy + (y1 - sy) * k - 8, bw = (x2 - x1) * k + 16, bh = (y2 - y1) * k + 16;
  g.save();
  g.shadowColor = "rgba(0, 0, 0, .35)";
  g.shadowBlur = 28;
  g.shadowOffsetY = 8;
  g.beginPath();
  g.roundRect(cx, cy, cw, ch, 22);
  g.fill();
  g.restore();
  g.save();
  g.beginPath();
  g.roundRect(cx, cy, cw, ch, 22);
  g.clip();
  g.drawImage(photo, sx, sy, sw, sh, cx, cy, cw, ch);
  g.fillStyle = "rgba(0, 0, 0, .55)";
  g.beginPath();
  g.rect(cx, cy, cw, ch);
  g.roundRect(bx, by, bw, bh, 10);
  g.fill("evenodd");
  g.restore();
  g.strokeStyle = "rgba(255, 255, 255, .9)";
  g.lineWidth = 2;
  g.beginPath();
  g.roundRect(bx, by, bw, bh, 10);
  g.stroke();
  overlay.style.opacity = 1;
}

photo.onload = draw;
addEventListener("resize", draw);