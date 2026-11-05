"""The agents. Each one has a single job, its own prompt, and a fallback chain of models (see models.py).
The orchestrator in server.py decides when each one runs."""
import io, re, json, base64, asyncio
from PIL import Image
import prompts
from models import meta, complete, SAM

BOX = re.compile(r"<\|box;x1=([\d.]+);y1=([\d.]+);x2=([\d.]+);y2=([\d.]+);w=([\d.]+);h=([\d.]+)")


# Image helpers

def url(frame):
    return "data:image/jpeg;base64," + frame


def small(frame, width=720):
    """Shrink a frame for quick decisions. The sharp original is kept for checks and highlights."""
    im = Image.open(io.BytesIO(base64.b64decode(frame)))
    if im.width <= width:
        return frame
    buf = io.BytesIO()
    im.resize((width, im.height * width // im.width), Image.LANCZOS).save(buf, "JPEG", quality=70)
    return base64.b64encode(buf.getvalue()).decode()


def zoom(frame, area):
    """Crop around a 0-1 box so small targets look big. Returns the crop and where it sits in the frame."""
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


def images(*frames):
    return [{"type": "image_url", "image_url": {"url": url(f)}} for f in frames if f]


# Talker: the only agent that speaks to the person

async def talk(history, event, frame=None, note=None):
    """Decide what to say after an event, without changing the history."""
    text = event + (f" [planner notes: {note}]" if note else "")
    content = [{"type": "text", "text": text}, *images(small(frame) if frame else None)]
    d, model = await complete("talker", [{"role": "system", "content": prompts.TALKER}, *history, {"role": "user", "content": content}])
    if d is None:
        return {"action": "silent", "failed": True}
    d["model"] = model
    return d


def remember(history, event, d):
    """Keep spoken turns and everything they said, so the talker knows the whole conversation."""
    if not d.get("failed") and (d.get("action") == "speak" or event.startswith("USER SAID")):
        reply = {k: v for k, v in d.items() if k != "model"}
        history += [{"role": "user", "content": event}, {"role": "assistant", "content": json.dumps(reply, ensure_ascii=False)}]
        del history[:-200]


# Checker: is the current step really done?

async def check_step(frame, step, target, area=None):
    """Look at the full view and a close-up of the target. Returns ("done" | "not_done" | "unclear", what it saw)."""
    closeup = zoom(frame, area)[0] if area else None
    content = [{"type": "text", "text": prompts.CHECKER.format(step=step, target=target or "unknown")},
               *images(small(frame, 1280), closeup)]
    d, model = await complete("checker", [{"role": "user", "content": content}])
    if d is None:
        return "unclear", None
    return d.get("status", "unclear"), f"{d.get('seen')} ({model})"


# Planner: thinks the whole task through in the background

async def plan_task(task, frame=None):
    """Short notes on the steps, blockers, questions and risky steps. Returns None if no model could plan."""
    content = [{"type": "text", "text": prompts.PLANNER.format(task=task)}, *images(small(frame) if frame else None)]
    notes, model = await complete("planner", [{"role": "user", "content": content}], want_json=False)
    if notes:
        print(f"planner ({model}):", " | ".join(line for line in notes.splitlines() if line.strip()))
    return notes


# Scam watcher: a quick look, then a careful confirmation

async def find_scam(frame):
    """Returns a short reason if two looks agree this is a scam, otherwise None."""
    picture = images(small(frame))
    d, _ = await complete("scam spotter", [{"role": "user", "content": [{"type": "text", "text": prompts.SCAM_SPOTTER}, *picture]}])
    if not d or not d.get("scam"):
        return None
    why = d.get("why") or "possible scam"
    d, model = await complete("scam confirmer", [{"role": "user", "content": [{"type": "text", "text": prompts.SCAM_CONFIRMER.format(why=why)}, *picture]}])
    if not d or not d.get("scam"):
        print("scam watcher: first look flagged it, but the confirmation did not agree:", why)
        return None
    print(f"scam watcher ({model}):", d.get("why") or why)
    return d.get("why") or why


# Translator: the app's few fixed messages, in any language

async def translate(words, lang):
    """Translate the app's start-screen messages into the phone's language. Returns None if no model could."""
    d, model = await complete("translator", [{"role": "user", "content": prompts.TRANSLATOR.format(lang=lang, words=json.dumps(words, ensure_ascii=False))}])
    if not d or set(d) != set(words) or not all(isinstance(v, str) and v for v in d.values()):
        return None
    print(f"translator ({model}): start screen in {lang}")
    return d


# Pointer: exactly where to press

async def sam(frame, target):
    """SAM 3.1 outlines every match. Returns a list of [x1, y1, x2, y2] as 0-1 fractions."""
    try:
        r = await meta.responses.create(model=SAM, input=[{"role": "user", "content": [
            {"type": "input_text", "text": target},
            {"type": "input_image", "image_url": url(frame)},
        ]}])
        return [[x1 / w, y1 / h, x2 / w, y2 / h] for x1, y1, x2, y2, w, h in
                (map(float, m.groups()) for m in BOX.finditer(r.output_text or ""))]
    except Exception as e:
        print("pointer: SAM failed:", repr(e))
        return []


async def find(image, target, view="close-up"):
    """Spark 1.3 boxes the target in a photo or a close-up. Returns 0-1 fractions, or None."""
    d, _ = await complete("pointer", [{"role": "user", "content": [{"type": "text", "text": prompts.POINTER.format(target=target, view=view)}, *images(image)]}])
    return fractions(d.get("area")) if d else None


async def locate(frame, target, area=None, kind=None):
    """Spark 1.3 finds the target in the whole photo, then boxes it exactly in a close-up while SAM outlines that kind of thing.
    The talker's rough area is only a fallback. SAM's outline is used only if it agrees. Returns 0-1 fractions, or None."""
    word = kind or target
    area = await find(small(frame, 1280), target, "photo") or fractions(area)
    if not area:
        boxes = await sam(frame, word)
        return boxes[0] if boxes else None
    crop, (l, t, w, h) = zoom(frame, area)
    exact, found = await asyncio.gather(find(crop, target), sam(crop, word))
    if exact:
        area = [l + exact[0] * w, t + exact[1] * h, l + exact[2] * w, t + exact[3] * h]
    boxes = [[l + b[0] * w, t + b[1] * h, l + b[2] * w, t + b[3] * h] for b in found]
    best = max(boxes, key=lambda b: overlap(b, area), default=None)
    agrees = best is not None and overlap(best, area) > .3
    print(f"pointer: SAM word {word!r}, matches {len(boxes)}, close-up found {bool(exact)}, SAM agrees {agrees}")
    return best if agrees else area
