import json
import os
from typing import Optional, Dict, List, Callable

from .config import Config
from .utils import retry_with_backoff


class LLMInterface:
    def __init__(self, config: Config):
        self.config = config
        self.provider = config.llm.get("provider", "openai")
        self.model = config.llm.get("model", "gpt-4o")
        self.api_key = config.llm.get("api_key") or self._resolve_api_key()
        if self.provider == "gemini" and self.model == "gpt-4o":
            self.model = "gemini-2.5-flash"
        self.api_base = config.llm.get("api_base")
        self.temperature = config.llm.get("temperature", 0.1)
        self.max_tokens = config.llm.get("max_tokens", 4096)
        self._client = None

    def _resolve_api_key(self) -> Optional[str]:
        key_map = {
            "openai": "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "gemini": "GEMINI_API_KEY",
            "ollama": None,
            "custom": None,
        }
        env_var = key_map.get(self.provider)
        return os.environ.get(env_var) if env_var else None

    @property
    def client(self):
        if self._client is not None:
            return self._client
        if self.provider == "openai":
            return self._init_openai()
        elif self.provider == "anthropic":
            return self._init_anthropic()
        elif self.provider == "gemini":
            return self._init_gemini()
        elif self.provider == "ollama":
            return self._init_ollama()
        else:
            return self._init_custom()

    def _init_openai(self):
        try:
            import openai
            kwargs = {"api_key": self.api_key}
            if self.api_base:
                kwargs["base_url"] = self.api_base
            self._client = openai.OpenAI(**kwargs)
            return self._client
        except ImportError:
            raise ImportError("openai package not installed. Install with: pip install openai")

    def _init_anthropic(self):
        try:
            import anthropic
            self._client = anthropic.Anthropic(api_key=self.api_key)
            return self._client
        except ImportError:
            raise ImportError("anthropic package not installed. Install with: pip install anthropic")

    def _init_gemini(self):
        try:
            import openai
            base = self.api_base or "https://generativelanguage.googleapis.com/v1beta/openai/"
            self._client = openai.OpenAI(api_key=self.api_key, base_url=base)
            return self._client
        except ImportError:
            raise ImportError("openai package required for Gemini. Install: pip install openai")

    def _init_ollama(self):
        class OllamaClient:
            def __init__(self, base_url, model):
                self.base_url = (base_url or "http://localhost:11434").rstrip("/")
                self.model = model

            def chat(self, messages, **kwargs):
                import requests
                payload = {
                    "model": self.model,
                    "messages": messages,
                    "stream": False,
                    **kwargs,
                }
                resp = requests.post(f"{self.base_url}/api/chat", json=payload)
                return resp.json()

        self._client = OllamaClient(self.api_base, self.model)
        return self._client

    def _init_custom(self):
        class CustomClient:
            def __init__(self, base_url, api_key, model):
                self.base_url = base_url.rstrip("/")
                self.api_key = api_key
                self.model = model

            def chat(self, messages, **kwargs):
                import requests
                headers = {"Content-Type": "application/json"}
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"
                payload = {
                    "model": self.model,
                    "messages": messages,
                    **kwargs,
                }
                resp = requests.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers, json=payload
                )
                return resp.json()

        base = self.api_base or "http://localhost:11434/v1"
        self._client = CustomClient(base, self.api_key, self.model)
        return self._client

    def query(self, system_prompt: str, context: str, user_query: str) -> str:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"{context}\n\nUser Question: {user_query}"},
        ]

        if self.provider == "openai":
            return self._query_openai(messages)
        elif self.provider == "anthropic":
            return self._query_anthropic(messages)
        elif self.provider == "gemini":
            return self._query_gemini(messages)
        elif self.provider == "ollama":
            return self._query_ollama(messages)
        else:
            return self._query_custom(messages)

    def stream_query(self, system_prompt: str, context: str,
                     user_query: str) -> str:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"{context}\n\nUser Question: {user_query}"},
        ]

        if self.provider in ("openai", "gemini"):
            return self._stream_openai(messages)
        else:
            return self.query(system_prompt, context, user_query)

    def _query_openai(self, messages: List[Dict]) -> str:
        def _call():
            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            return response.choices[0].message.content or ""
        return retry_with_backoff(_call)

    def _stream_openai(self, messages: List[Dict]) -> str:
        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            stream=True,
        )
        parts = []
        for chunk in response:
            delta = chunk.choices[0].delta.content or ""
            parts.append(delta)
            print(delta, end="", flush=True)
        print()
        return "".join(parts)

    def _query_anthropic(self, messages: List[Dict]) -> str:
        system = messages[0]["content"]
        user = messages[-1]["content"]
        def _call():
            response = self.client.messages.create(
                model=self.model,
                system=system,
                messages=[{"role": "user", "content": user}],
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            )
            return response.content[0].text if response.content else ""
        return retry_with_backoff(_call)

    def _query_gemini(self, messages: List[Dict]) -> str:
        return self._query_openai(messages)

    def _query_ollama(self, messages: List[Dict]) -> str:
        def _call():
            response = self.client.chat(
                messages=messages,
                options={"temperature": self.temperature, "num_predict": self.max_tokens},
            )
            return response.get("message", {}).get("content", "")
        return retry_with_backoff(_call)

    def _query_custom(self, messages: List[Dict]) -> str:
        def _call():
            response = self.client.chat(
                messages=messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            if isinstance(response, dict):
                choices = response.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "")
            return str(response)
        return retry_with_backoff(_call)
