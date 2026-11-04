"""The orchestrator. One Session per phone decides which agent runs when:
- the person finishes speaking: the talker answers (it may already have started while they were talking)
- the camera picture changes: the checker decides if the step is done, and only then the talker speaks
- a new task starts: the planner thinks ahead in the background
- the camera settles on something new: the scam watcher looks in the background
- they ask "where?": the pointer finds the exact spot
The talker is the only agent that speaks, and it speaks one turn at a time."""
import re, json, time, asyncio
from pathlib import Path
from fastapi import FastAPI, WebSocket
from fastapi.staticfiles import StaticFiles
from agents import talk, remember, check_step, plan_task, find_scam, translate, locate, fractions
from voice import Voice

WAIT_SECONDS = 30
SCAN_EVERY, WARN_AGAIN_AFTER = 6, 60
GUESS_AFTER = 0.3
SORRY = {"sorry": "Sorry, please say that again."}
app = FastAPI()


def same_words(text):
    """Text reduced to its letters, so "Hello." and "hello" match."""
    return re.sub(r"[\W_]+", "", text.lower())


def word_set(text):
    """The meaningful words, so short common words like "is" or "it" do not count as an echo."""
    return {w for w in (w.strip(".,!?।") for w in text.lower().split()) if len(w) > 3}


translations = {}


async def in_language(english, lang):
    """English messages in any language, translated once and then remembered. Falls back to English."""
    if lang.lower().startswith("en"):
        return english
    key = lang, json.dumps(english, sort_keys=True)
    if key not in translations:
        done = await translate(english, lang)
        if not done:
            return english
        translations[key] = done
    return translations[key]


@app.post("/words")
async def words(body: dict):
    """The start screen's messages in the phone's language."""
    return await in_language(body.get("words", {}), body.get("lang", "en"))


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
        self.sorry, self.sorry_lang = SORRY["sorry"], "en-US"
        self.area, self.scanning, self.scanned_at, self.warned_at = None, False, 0, 0
        self.plan, self.guess, self.guess_timer = None, None, None
        self.voice = Voice(self.on_partial, self.on_final)

    async def send(self, **msg):
        await self.ws.send_json(msg)

    # Listening

    def echo(self, text):
        """True if the mic only picked up the guide's own voice."""
        words = word_set(text)
        return bool(words) and time.time() < self.say_until and len(words & self.last_say) >= .6 * len(words)

    async def on_partial(self, text):
        if self.echo(text):
            return
        self.heard, self.say_until = time.time(), 0
        self.stop_check()
        if self.guess_timer:
            self.guess_timer.cancel()
        self.guess_timer = asyncio.create_task(self.think_ahead(text))
        await self.send(type="caption", text=text)

    async def think_ahead(self, text):
        """After a short pause in their speech, start the talker with what they have said so far."""
        await asyncio.sleep(GUESS_AFTER)
        words = " ".join(self.words + [text])
        self.drop_guess()
        event = self.with_notes(f"USER SAID: {words}", self.frame)
        self.guess = same_words(words), asyncio.create_task(talk(list(self.history), event, self.frame, self.plan))

    def drop_guess(self):
        if self.guess:
            self.guess[1].cancel()
            self.guess = None

    async def on_final(self, text):
        if self.echo(text):
            return
        self.stop_check()
        if self.pause:
            self.pause.cancel()
        if self.guess_timer:
            self.guess_timer.cancel()
        self.words.append(text)
        self.pause = asyncio.create_task(self.reply())

    async def reply(self):
        """Answer right away. If they keep talking, the next final cancels this and answers everything said."""
        used, text, ready = len(self.words), " ".join(self.words), None
        if self.guess and self.guess[0] == same_words(text):
            ready, self.guess = self.guess[1], None
            print("thought ahead: using the answer started while they were talking")
        else:
            self.drop_guess()
        self.said += 1
        await self.handle(f"USER SAID: {text}", visible=True, turn=self.said, ready=ready)
        del self.words[:used]

    # Watching

    async def on_frame(self, frame, quiet=False):
        self.frame = frame
        print(f"frame received ({len(frame) // 1024} KB){' quiet' if quiet else ''}, phase: {self.phase}")
        if quiet:
            return
        asyncio.create_task(self.watch_for_scams(frame))
        if self.lock.locked():
            self.dirty = True
        else:
            await self.screen_event()

    async def screen_event(self):
        if self.phase == "need_screen":
            await self.handle("SCREEN READY", visible=True)
        elif self.phase == "waiting_action" and self.step and not (self.check and not self.check.done()):
            self.check = asyncio.create_task(self.check_progress())

    def stop_check(self):
        if self.check:
            self.check.cancel()

    async def check_progress(self):
        """The checker looks at the new picture. Only a clearly finished step is passed to the talker."""
        step, target = self.step, self.target
        status, seen = await check_step(self.frame, step, target, fractions(self.area))
        print(f"checker: {status} | {seen}")
        if status == "done" and step == self.step:
            await self.handle(f'STEP DONE: they finished "{step}". The checker saw: {seen}')

    async def watch_for_scams(self, frame):
        """The scam watcher looks at new pictures in the background and warns them without being asked."""
        now = time.time()
        if self.scanning or now - self.scanned_at < SCAN_EVERY or now - self.warned_at < WARN_AGAIN_AFTER:
            return
        self.scanning, self.scanned_at = True, now
        try:
            why = await find_scam(frame)
        finally:
            self.scanning = False
        if why:
            self.warned_at = time.time()
            await self.handle(f"SCAM FOUND: {why}", visible=True)

    async def make_plan(self, task, frame):
        """The planner thinks the whole task through in the background, then the talker shares anything urgent.
        If the task has already moved on, the plan is kept for looking ahead but not spoken."""
        self.plan, step = None, self.step
        notes = await plan_task(task, frame)
        if notes:
            self.plan = notes
            if self.step == step and self.phase != "done":
                await self.handle(f"PLAN READY: {notes}")

    # Speaking

    def phone_speaks(self, lang):
        return lang.split("-")[0].lower() in self.phone_langs

    def with_notes(self, event, frame):
        if not frame:
            event += " [no camera image received yet]"
        if self.language and not self.phone_speaks(self.lang):
            event += f" [this phone has no {self.language} voice, so your replies are shown as text but not heard]"
        return event

    async def handle(self, event, visible=False, turn=None, ready=None):
        """Run the talker for one event, one at a time, and send what it says."""
        start, spoken = time.time(), event.startswith("USER SAID")
        async with self.lock:
            if turn and turn != self.said:
                print(f"skipped, newer words came in: {event[:40]}")
                return
            if visible:
                await self.send(type="state", state="thinking")
            frame, previous = self.frame, self.target
            event = self.with_notes(event, frame)
            note = self.plan if self.plan and not event.startswith("PLAN") else None
            d = await (ready or talk(self.history, event, frame, note))
            remember(self.history, event, d)
            if d.get("failed") and spoken:
                d.update(action="speak", say=self.sorry, lang=self.sorry_lang, step=self.step)
            was, self.phase = self.phase, d.get("phase", self.phase)
            if spoken and was == "listening" and self.phase != "listening":
                said = [m["content"].split(" [")[0].removeprefix("USER SAID: ") for m in self.history if m["role"] == "user" and m["content"].startswith("USER SAID")]
                asyncio.create_task(self.make_plan(" / ".join(said[-3:]), frame))
            elif self.phase == "done":
                self.plan = None
            if d.get("target") != previous or d.get("area"):
                self.area = d.get("area")
            self.target, self.lang = d.get("target", self.target), d.get("lang", self.lang)
            if d.get("language") and d["language"] != self.language:
                self.language = d["language"]
                print("language is now", self.language)
                asyncio.create_task(self.voice.listen_for(self.language))
                asyncio.create_task(self.prepare_sorry(self.lang))
            step = d.get("step")
            show = spoken and d.get("highlight") and d.get("target") == previous
            asked = was == self.phase == "need_screen"
            repeat = event.startswith(("SCREEN", "STEP DONE")) and (asked or (step and step == self.step))
            if d.get("action") == "speak" and d.get("say") and not repeat and self.heard <= start:
                self.step = step
                self.last_say, self.say_until = word_set(d["say"]), time.time() + min(15, 1.5 + len(d["say"]) * .09) + 1
                await self.send(type="say", text=d["say"], lang=d.get("lang", "en-US"), warning=bool(d.get("warning")))
                self.restart_waiter()
            elif visible:
                await self.send(type="state", state="idle")
            print(f"{event[:40]} | say: {d.get('say', '')} | step: {step} | phase: {self.phase} | highlight: {show} | {d.get('model')} | {time.time() - start:.1f}s")
        if show and self.target:
            asyncio.create_task(self.highlight(frame, self.target, d.get("area"), d.get("kind")))
        elif self.target != previous:
            await self.send(type="box", box=None)
        if self.dirty:
            self.dirty = False
            await self.screen_event()

    async def prepare_sorry(self, lang):
        """Have the apology ready in their language, in case every model fails later."""
        words = await in_language(SORRY, lang)
        translated = words is not SORRY or lang.lower().startswith("en")
        self.sorry, self.sorry_lang = words["sorry"], lang if translated else "en-US"

    async def highlight(self, frame, target, area, kind):
        """The pointer finds the exact spot, then the phone shows it."""
        start = time.time()
        box = await locate(frame, target, area, kind) if frame and target else None
        if target == self.target:
            await self.send(type="box", box=box, image=frame if box else None)
        print(f"pointer: box for {target!r}: {box} | {time.time() - start:.1f}s")

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


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="static")
