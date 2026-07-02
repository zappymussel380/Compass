"""
Bhai Bhai Pipeline
------------------
Plain Ollama/Gemma passthrough - no RAG, no special context.
Use for general questions, drafting, brainstorming, etc.
"""

from typing import Generator
from pydantic import BaseModel
import httpx
import json


class Pipeline:
    class Valves(BaseModel):
        OLLAMA_BASE_URL: str = "http://ollama:11434"
        MODEL: str = "gemma4:e4b"
        SYSTEM_PROMPT: str = (
            "You are Bhai Bhai, a helpful general-purpose assistant. "
            "Answer clearly and concisely. You are running locally on a private home server."
        )

    def __init__(self):
        self.name = "Bhai Bhai"
        self.valves = self.Valves()

    async def on_startup(self):
        print(f"[Bhai Bhai] Pipeline started. Model: {self.valves.MODEL}")

    async def on_shutdown(self):
        print("[Bhai Bhai] Pipeline shutting down.")

    @staticmethod
    def _to_text(content) -> str:
        """Normalize OpenAI-style content (string or list) to a plain string for Ollama."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        return str(content)

    def pipe(
        self,
        user_message: str,
        model_id: str,
        messages: list[dict],
        body: dict,
    ) -> Generator[str, None, None]:

        # Skip Ollama entirely for background tasks (title/tag/follow-up generation).
        # OpenWebUI falls back to its own defaults; this keeps the model free.
        if bool(body.get("task")) or not body.get("stream", True):
            return

        ollama_messages = []

        has_system = any(m.get("role") == "system" for m in messages)
        if not has_system:
            ollama_messages.append({
                "role": "system",
                "content": self.valves.SYSTEM_PROMPT,
            })

        for msg in messages:
            if msg.get("role") in ("user", "assistant", "system"):
                ollama_messages.append({
                    "role": msg["role"],
                    "content": self._to_text(msg["content"]),
                })

        payload = {
            "model": self.valves.MODEL,
            "messages": ollama_messages,
            "stream": True,
            "options": {
                "num_predict": 4096,
                "temperature": body.get("temperature", 0.7),
            },
        }

        url = f"{self.valves.OLLAMA_BASE_URL}/api/chat"

        try:
            with httpx.Client(timeout=120.0) as client:
                with client.stream("POST", url, json=payload) as response:
                    # Read error body while the stream is still open — accessing
                    # response.text after the context exits raises ResponseNotRead.
                    if response.status_code != 200:
                        error_body = response.read().decode("utf-8", errors="replace")
                        yield f"\n\n⚠️ **Ollama error {response.status_code}:** {error_body}"
                        return
                    for line in response.iter_lines():
                        if not line.strip():
                            continue
                        try:
                            chunk = json.loads(line)
                            token = chunk.get("message", {}).get("content", "")
                            if token:
                                yield token
                            if chunk.get("done"):
                                break
                        except json.JSONDecodeError:
                            continue

        except httpx.ConnectError:
            yield (
                "\n\n⚠️ **Cannot reach Ollama.** "
                f"Is it running at `{self.valves.OLLAMA_BASE_URL}`?"
            )
        except Exception as e:
            yield f"\n\n⚠️ **Unexpected error:** {str(e)}"
