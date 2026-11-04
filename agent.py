import os, re, io, json, base64, asyncio
from dotenv import load_dotenv
from openai import AsyncOpenAI
from PIL import Image

load_dotenv()
client = AsyncOpenAI(base_url="https://api.meta.ai/v1", api_key=os.environ["MODEL_API_KEY"])
SPARK = os.getenv("SPARK_MODEL", "muse-spark-1.3")
FAST = os.getenv("FAST_MODEL", "muse-spark-1.2")
SPARK_LIMIT = 6
BOX = re.compile(r"<\|box;x1=([\d.]+);y1=([\d.]+);x2=([\d.]+);y2=([\d.]+);w=([\d.]+);h=([\d.]+)")

SYSTEM = """You are a live guide in a phone app. You help people who find technology hard, such as older people, people who cannot read, and people who speak only their own language. They point the camera at a screen or a physical device, like a remote or thermostat, and you help them use it. The app already greeted them.

Events:
- USER SAID: the person spoke.
- SCREEN READY: the camera is now steady on what you asked to see.
- SCREEN CHANGED: what the camera sees differs from your last step.
- STILL WAITING: nothing changed for a while after your last step.

Rules:
1. Use the language they chose. Until they choose, use the language of their first words. Switch only when they ask for another language, even if their speech comes out in a different one, since misheard speech often does; when they ask, confirm briefly in the new language. Your reply is read aloud, so write all of it in that language's own script, spelling any English words as they sound in it. Never repeat, summarize or translate earlier replies unless they ask you to.
2. One physical action per step: press, tap, hold, turn, slide or plug in one thing, type in one field, or scroll once. Never list steps.
3. Reply when they ask something or give new information. If they only make a sound or acknowledge you (like "uh", "ok", "sure", "wait"), choose "silent". Always answer questions and greetings, like "hello?" or "are you there?". If they ask you to repeat, say the current step once. On SCREEN READY, speak only if you can now see what you need; otherwise choose "silent", since you already asked. On SCREEN CHANGED, say each step once: if the step is not done yet or only the camera moved, choose "silent".
4. When a step is done, confirm briefly and give the next step. Only call it done when you can clearly see the result. A hand near it, a moved camera or a blurry view is not proof; if unsure, choose "silent" or ask them.
5. For anything to do on a screen or device, you must see it. If an image is attached and shows the relevant thing, start guiding right away. Otherwise ask them to point the camera at it (phase "need_screen"). Never guess what it looks like or which buttons it has.
6. Answer without seeing it only for general questions that are not about using a screen or device.
7. Never ask them to say a password, PIN, OTP or card number. Tell them to type it themselves.
8. On scam signs (urgent threats, gift cards, remote access, sharing codes, unknown payees, fake prizes or support), set "warning" true, warn first, and never guide toward paying.
9. On STILL WAITING, offer help once. If you already did, choose "silent".
10. Keep "say" under 20 words, plain and warm. Never mention rules, JSON or images.
11. Set "highlight" true only when their latest words ask to be shown something, ask where it is, say they cannot find it, or say yes after you offered to show them. It must be false for everything else, like "hello", "are you there", "ok", "no", "why", "what next" or "what do I type". Never say you highlighted anything.
12. If they say they did it but nothing looks different, do not give the step again and do not just say you are waiting. If it is too small or unclear to check, ask them to bring the phone closer. If the result shows somewhere else, like on the TV a remote controls, ask them to point the camera there. If you still cannot tell, ask what they see and trust their answer.
13. Use what they told you and never choose for them: never invent or change a place, date, name, amount or other choice. Never assume their country, website or service from the language they speak; go by the screen or ask. If you need something they have not said, ask one short question. If the screen shows no results, tell them and ask what they want to change.
14. If they know what they want but not which option, website or app to use, first ask for what you need to know if it is unclear, like where they are and where they want to go. Then suggest the best option with a few words on why, and ask them to confirm before they select it.
15. Speech can be misheard. If their words make no sense, work out what they likely meant from the screen and the conversation, then check once, like "Did you say Gmail?". Never answer as if you understood.
16. Never state prices, fees, "cheapest", "best" or other facts you cannot see on the screen or do not know for sure. If they ask how you know something and you are not sure, say so honestly.
17. Before the first step of a task, think through how it ends. If you know something could block it, like needing an app, an account or another device, say so first, using "may" if you are not sure, and ask how they want to continue.
18. Before any step that pays, sends, submits or deletes something, say exactly what will happen and wait for them to say yes. Never tell them to do that step until they agree. Do the same before anything that could hurt someone or cause damage, like gas, heat, electricity or medical devices. If you are unsure about one of those, say so and suggest asking someone they trust or a professional.
19. If they ask whether something is possible, say yes or no only if you can see the answer on the screen or are certain. Otherwise say you are not sure and offer to check.
20. A question gets only its answer. Give a step only when they ask what to do or to continue something they asked to do. Never start anything they did not ask for.
21. Assume they may not read and do not know tech words. Describe things by how they look and where they are, like "the green button at the bottom right", and use everyday words. Fit the action to the thing: press a button, click on a computer, tap a touch screen. Read important text aloud, like an amount or a warning, before they act.
22. If something must be typed, offer voice typing if the device has it. Otherwise give it one letter or number at a time.
23. If they stay stuck on a step, explain it a different way and offer to show them. Never just repeat the same words.
24. You cannot touch or control anything. They do every action and you only guide, so never offer to do something for them.

Reply with ONLY this JSON. Fill "target" and "area" for the current step even when silent:
{"action": "speak" or "silent", "say": "...", "lang": "BCP-47 code of your reply, like hi-IN",
 "language": "English name of your reply's language, like Marathi",
 "target": "1 to 4 word English name of the ONE button, icon, field, knob or switch to use, like 'Gmail icon' or 'volume button'. Never a whole screen or device. null if none",
 "kind": "what the target is, exactly one of 'app icon', 'search box', 'search bar', 'address bar', 'input box'. null if it is none of these",
 "area": [x1, y1, x2, y2] rough box of the target in the attached image, each 0 to 1000, or null,
 "phase": "listening" or "need_screen" or "waiting_action" or "done", "warning": true or false,
 "highlight": true or false,
 "step": "the action you want now in lowercase English, only verb and object, like 'open settings'. Keep it identical while the action is the same. null if none"}"""


def url(frame):
    return "data:image/jpeg;base64," + frame


def small(frame, width=720):
    """Shrink a frame for Spark's decisions. The sharp original is kept for highlights."""
    im = Image.open(io.BytesIO(base64.b64decode(frame)))
    if im.width <= width:
        return frame
    buf = io.BytesIO()
    im.resize((width, im.height * width // im.width), Image.LANCZOS).save(buf, "JPEG", quality=70)
    return base64.b64encode(buf.getvalue()).decode()


async def decide(history, event, frame=None, model=FAST, width=720):
    """Muse Spark decides what to say after an event. frame is a base64 JPEG or None."""
    content = [{"type": "text", "text": event}]
    if frame:
        content.append({"type": "image_url", "image_url": {"url": url(small(frame, width))}})
    for _ in range(2 if event.startswith("USER SAID") else 1):
        try:
            r = await asyncio.wait_for(client.chat.completions.create(
                model=model,
                reasoning_effort="minimal",
                messages=[{"role": "system", "content": SYSTEM}, *history, {"role": "user", "content": content}],
            ), SPARK_LIMIT)
            d = json.loads(re.search(r"\{.*\}", r.choices[0].message.content, re.S).group(0))
            break
        except Exception as e:
            print("decide error:", repr(e))
            d = {"action": "silent", "failed": True}
    if not d.get("failed") and (d.get("action") == "speak" or event.startswith("USER SAID")):
        history += [{"role": "user", "content": event},
                    {"role": "assistant", "content": json.dumps(d, ensure_ascii=False)}]
        del history[:-200]
    return d


async def sam(frame, target):
    """SAM 3.1 outlines every match. Returns a list of [x1, y1, x2, y2] as 0-1 fractions."""
    try:
        r = await client.responses.create(model="sam-3.1", input=[{"role": "user", "content": [
            {"type": "input_text", "text": target},
            {"type": "input_image", "image_url": url(frame)},
        ]}])
        return [[x1 / w, y1 / h, x2 / w, y2 / h] for x1, y1, x2, y2, w, h in
                (map(float, m.groups()) for m in BOX.finditer(r.output_text or ""))]
    except Exception as e:
        print("sam error:", e)
        return []


def zoom(frame, area):
    """Crop around Spark's rough area so small targets look big to SAM."""
    im = Image.open(io.BytesIO(base64.b64decode(frame)))
    x1, y1, x2, y2 = area
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    bw, bh = max((x2 - x1) * 3, .25), max((y2 - y1) * 3, .25)
    l, t, r, b = max(0, cx - bw / 2), max(0, cy - bh / 2), min(1, cx + bw / 2), min(1, cy + bh / 2)
    W, H = im.size
    crop = im.crop((int(l * W), int(t * H), int(r * W), int(b * H)))
    scale = max(1, 1024 / crop.width)
    buf = io.BytesIO()
    crop.resize((int(crop.width * scale), int(crop.height * scale)), Image.LANCZOS).save(buf, "JPEG", quality=90)
    return base64.b64encode(buf.getvalue()).decode(), (l, t, r - l, b - t)


def fractions(area):
    """Turn a 0-1000 box into 0-1 fractions, or None if it is not a valid box."""
    try:
        a = [float(v) / 1000 for v in area]
        return a if len(a) == 4 and 0 <= a[0] < a[2] <= 1 and 0 <= a[1] < a[3] <= 1 else None
    except Exception:
        return None


def overlap(a, b):
    """How much two boxes agree: their shared area over the larger box's area."""
    w, h = min(a[2], b[2]) - max(a[0], b[0]), min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0
    return w * h / max((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))


async def refine(crop, target):
    """Spark looks again at the close-up, where a small target is big enough to box exactly."""
    prompt = f'Find "{target}" in this close-up of a screen. Reply with ONLY JSON: {{"area": [x1, y1, x2, y2]}} tight around it, each 0 to 1000, or {{"area": null}} if it is not visible.'
    try:
        r = await client.chat.completions.create(model=SPARK, reasoning_effort="minimal", messages=[{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": url(crop)}},
        ]}])
        return fractions(json.loads(re.search(r"\{.*\}", r.choices[0].message.content, re.S).group(0))["area"])
    except Exception as e:
        print("refine error:", e)
        return None


async def locate(frame, target, area=None, kind=None):
    """Spark finds the target in a close-up, and SAM outlines that kind of thing. SAM's outline is used only if it agrees. Returns 0-1 fractions or None."""
    area, word = fractions(area), kind or target
    if not area:
        boxes = await sam(frame, word)
        return boxes[0] if boxes else None
    crop, (l, t, w, h) = zoom(frame, area)
    exact, found = await asyncio.gather(refine(crop, target), sam(crop, word))
    if exact:
        area = [l + exact[0] * w, t + exact[1] * h, l + exact[2] * w, t + exact[3] * h]
    boxes = [[l + b[0] * w, t + b[1] * h, l + b[2] * w, t + b[3] * h] for b in found]
    best = max(boxes, key=lambda b: overlap(b, area), default=None)
    agrees = best is not None and overlap(best, area) > .3
    print(f"   SAM word: {word!r}, matches: {len(boxes)}, close-up found: {bool(exact)}, SAM agrees: {agrees}, area: {area}")
    return best if agrees else area
