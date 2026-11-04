"""Every model TARS uses, and the fallback chain each agent asks in order.
If a model is slow or fails, the next one in the chain answers, and the log says which one did."""
import os, re, json, asyncio
from typing import NamedTuple
from dotenv import load_dotenv
from openai import AsyncOpenAI

load_dotenv()
meta = AsyncOpenAI(base_url="https://api.meta.ai/v1", api_key=os.environ["MODEL_API_KEY"])
BASETEN_KEY = os.getenv("BASETEN_API_KEY")
baseten = AsyncOpenAI(base_url="https://inference.baseten.co/v1", api_key=BASETEN_KEY) if BASETEN_KEY else None
SAM = "sam-3.1"


class Model(NamedTuple):
    name: str
    client: AsyncOpenAI
    seconds: float
    options: dict


QUICK = Model(os.getenv("FAST_MODEL", "muse-spark-1.2"), meta, 4, {"reasoning_effort": "minimal"})
CAREFUL = Model(os.getenv("SPARK_MODEL", "muse-spark-1.3"), meta, 8, {"reasoning_effort": "minimal"})
OPEN = Model(os.getenv("OPEN_MODEL", "meta-models/muse-spark-open-1026"), baseten, 20, {"max_tokens": 3000}) if baseten else None


def chain(*models):
    return [m for m in models if m]


# One backup each. Where possible it runs on Baseten, so a slow or failing Meta API is covered.
CHAINS = {
    "talker": chain(QUICK, OPEN),
    "checker": chain(CAREFUL, OPEN),
    "planner": chain(OPEN, CAREFUL),
    "scam spotter": chain(QUICK, OPEN),
    "scam confirmer": chain(CAREFUL, OPEN),
    "pointer": chain(CAREFUL, QUICK),
    "translator": chain(QUICK._replace(seconds=12), OPEN),
}


def parse_json(text):
    return json.loads(re.search(r"\{.*\}", text, re.S).group(0))


async def complete(agent, messages, want_json=True):
    """Ask the agent's models in order until one gives a usable answer. Returns (answer, model name) or (None, None)."""
    for model in CHAINS[agent]:
        try:
            r = await asyncio.wait_for(model.client.chat.completions.create(model=model.name, messages=messages, **model.options), model.seconds)
            text = (r.choices[0].message.content or "").strip()
            if not text:
                raise ValueError("empty answer")
            return (parse_json(text) if want_json else text), model.name
        except Exception as e:
            print(f"{agent}: {model.name} failed ({type(e).__name__}), trying the next model")
    print(f"{agent}: every model failed")
    return None, None
