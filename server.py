import json, time, asyncio
from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
from agent import decide, locate, SPARK, FAST
from voice import Voice

WAIT_SECONDS = 30
PAUSE_SECONDS = 0.8
SORRY = {
    "en": "Sorry, please say that again.",
    "hi": "माफ़ कीजिए, कृपया फिर से कहिए।",
    "mr": "माफ करा, कृपया पुन्हा सांगा.",
    "or": "କ୍ଷମା କରିବେ, ଦୟାକରି ପୁଣି କୁହନ୍ତୁ।",
}
app = FastAPI()


def word_set(text):
    return {w.strip(".,!?।") for w in text.lower().split()}


@app.middleware("http")
async def no_cache(request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


class Session:
    def __init__(self, ws):
        self.ws, self.history, self.frame, self.target = ws, [], None, None
        self.phase, self.waiter, self.dirty = "listening", None, False
        self.lock, self.step, self.check = asyncio.Lock(), None, None
        self.heard, self.said = 0, 0
        self.words, self.pause, self.last_say, self.say_until = [], None, set(), 0
        self.lang, self.phone_langs, self.language = "en-US", {"en"}, None
        self.voice = Voice(self.on_partial, self.on_final)

    async def send(self, **msg):
        await self.ws.send_json(msg)

    def stop_check(self):
        if self.check:
            self.check.cancel()

    def echo(self, text):
        """True if the mic only picked up the guide's own voice."""
        words = word_set(text)
        return time.time() < self.say_until and len(words & self.last_say) >= .6 * len(words)

    def stop_pause(self):
        if self.pause:
            self.pause.cancel()

    async def on_partial(self, text):
        if self.echo(text):
            return
        self.heard, self.say_until = time.time(), 0
        self.stop_check()
        self.stop_pause()
        await self.send(type="caption", text=text)

    async def on_final(self, text):
        if self.echo(text):
            return
        self.stop_check()
        self.stop_pause()
        self.words.append(text)
        self.pause = asyncio.create_task(self.reply())

    async def reply(self):
        await asyncio.sleep(PAUSE_SECONDS)
        text = " ".join(self.words)
        self.words.clear()
        self.said += 1
        asyncio.create_task(self.handle(f"USER SAID: {text}", visible=True, turn=self.said))

    async def on_frame(self, frame, quiet=False):
        self.frame = frame
        print(f"frame received ({len(frame) // 1024} KB){' quiet' if quiet else ''}, phase: {self.phase}")
        if quiet:
            return
        if self.lock.locked():
            self.dirty = True
        else:
            await self.screen_event()

    async def screen_event(self):
        if self.phase == "need_screen":
            await self.handle("SCREEN READY", visible=True)
        elif self.phase == "waiting_action":
            self.check = asyncio.create_task(self.handle("SCREEN CHANGED"))

    async def handle(self, event, visible=False, turn=None):
        start, spoken = time.time(), event.startswith("USER SAID")
        async with self.lock:
            if turn and turn != self.said:
                print(f"skipped, newer words came in: {event[:40]}")
                return
            if visible:
                await self.send(type="state", state="thinking")
            frame, previous = self.frame, self.target
            if not frame:
                event += " [no camera image received yet]"
            if self.language and self.lang.split("-")[0].lower() not in self.phone_langs:
                event += f" [this phone has no {self.language} voice, so your replies are shown as text but not heard]"
            screen = event.startswith("SCREEN")
            d = await decide(self.history, event, frame, model=SPARK if screen else FAST, width=1280 if screen else 720)
            if d.get("failed") and spoken:
                base = self.lang.split("-")[0]
                d.update(action="speak", say=SORRY.get(base, SORRY["en"]), lang=self.lang if base in SORRY else "en-US", step=self.step)
            was, self.phase = self.phase, d.get("phase", self.phase)
            self.target, self.lang = d.get("target", self.target), d.get("lang", self.lang)
            if d.get("language") and d["language"] != self.language:
                self.language = d["language"]
                print("language is now", self.language)
                asyncio.create_task(self.voice.listen_for(self.language))
            step, show = d.get("step"), spoken and d.get("highlight")
            asked = was == self.phase == "need_screen"
            repeat = event.startswith("SCREEN") and (asked or (step and step == self.step))
            if d.get("action") == "speak" and d.get("say") and not repeat and self.heard <= start:
                self.step = step
                self.last_say, self.say_until = word_set(d["say"]), time.time() + min(15, 1.5 + len(d["say"]) * .09) + 1
                await self.send(type="say", text=d["say"], lang=d.get("lang", "en-US"), warning=bool(d.get("warning")))
                self.restart_waiter()
            elif visible:
                await self.send(type="state", state="idle")
            print(f"{event[:40]} | say: {d.get('say', '')} | target: {self.target} | phase: {self.phase} | step: {step} | repeat: {repeat} | highlight: {show} | {time.time() - start:.1f}s")
        if show and self.target:
            asyncio.create_task(self.highlight(frame, self.target, d.get("area"), d.get("kind")))
        elif self.target != previous:
            await self.send(type="box", box=None)
        if self.dirty:
            self.dirty = False
            await self.screen_event()

    async def highlight(self, frame, target, area, kind):
        start = time.time()
        box = await locate(frame, target, area, kind) if frame and target else None
        if target == self.target:
            await self.send(type="box", box=box, image=frame if box else None)
        if target:
            print(f"   box for {target!r}: {box} | {time.time() - start:.1f}s")

    def restart_waiter(self):
        if self.waiter:
            self.waiter.cancel()
        if self.phase == "waiting_action":
            self.waiter = asyncio.create_task(self.wait())

    async def wait(self):
        await asyncio.sleep(WAIT_SECONDS)
        await self.handle("STILL WAITING")

    async def close(self):
        if self.waiter:
            self.waiter.cancel()
        await self.voice.close()


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    s = Session(ws)
    try:
        while True:
            m = await ws.receive()
            if m["type"] == "websocket.disconnect":
                break
            if m.get("bytes"):
                await s.voice.send(m["bytes"])
            elif m.get("text"):
                d = json.loads(m["text"])
                if d.get("type") == "frame":
                    asyncio.create_task(s.on_frame(d["image"], d.get("quiet", False)))
                elif d.get("type") == "voices":
                    print("phone voices:", d["langs"])
                    s.phone_langs |= {lang.split("-")[0].lower() for lang in d["langs"]}
    finally:
        await s.close()


app.mount("/", StaticFiles(directory="static", html=True), name="static")