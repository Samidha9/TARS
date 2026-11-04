import os, json, uuid, asyncio
import websockets
from dotenv import load_dotenv

load_dotenv()
URL = "wss://api.meta.ai/v1/asr/realtime"
KEY = os.environ["MODEL_API_KEY"]
DEFAULT_BIAS = ["English", "Hindi", "Marathi", "Odia"]


class Voice:
    """One live Muse Voice session. Feed it 16 kHz mono PCM16 audio with send()."""

    def __init__(self, on_partial, on_final):
        self.on_partial, self.on_final = on_partial, on_final
        self.ws, self.task = None, None
        self.lock = asyncio.Lock()
        self.bias = DEFAULT_BIAS

    async def start(self):
        self.ws = await websockets.connect(f"{URL}?sessionId=stream-{uuid.uuid4()}", max_size=None)
        await self.ws.send(json.dumps({
            "authorization": {"accessToken": f"Bearer {KEY}"},
            "audioEncoding": "PCM_16KHZ",
            "model": "muse-voice-transcribe-1.0",
            "mode": "ENDPOINTING",
            "partialMode": "CUMULATIVE",
            "emitAudioProgress": False,
            "languageBias": self.bias,
        }))
        ack = json.loads(await asyncio.wait_for(self.ws.recv(), 10))
        if "sessionId" not in ack:
            raise RuntimeError(f"Muse Voice rejected the session: {ack}")
        self.task = asyncio.create_task(self._read())
        return ack["sessionId"]

    async def send(self, pcm):
        async with self.lock:
            if not self.ws or self.ws.close_code is not None:
                try:
                    await self.start()
                except Exception as e:
                    if self.bias == DEFAULT_BIAS:
                        raise
                    print(f"voice could not listen for {self.bias}, using the default languages:", e)
                    self.bias = DEFAULT_BIAS
                    await self.start()
        try:
            await self.ws.send(pcm)
        except websockets.ConnectionClosed:
            pass

    async def listen_for(self, language):
        """Listen mainly for this language from now on. The next audio reconnects with it."""
        bias = list(dict.fromkeys([language, "English", "Hindi"]))
        if bias == self.bias:
            return
        self.bias = bias
        if self.ws and self.ws.close_code is None:
            await self.ws.close()

    async def _read(self):
        try:
            async for msg in self.ws:
                if isinstance(msg, bytes):
                    continue
                m = json.loads(msg)
                t, text = m.get("type"), m.get("transcript", "")
                if t == "transcript" and text:
                    await self.on_partial(text)
                elif t == "speechComplete" and text:
                    await self.on_final(text)
                elif t == "error":
                    print("voice error:", m)
        except Exception as e:
            if not isinstance(e, websockets.ConnectionClosed):
                print("voice read error:", e)
        finally:
            await self.ws.close()

    async def close(self):
        if self.ws and self.ws.close_code is None:
            try:
                await self.ws.send(json.dumps({"type": "endStream"}))
            except websockets.ConnectionClosed:
                pass
            await self.ws.close()
        if self.task:
            self.task.cancel()
