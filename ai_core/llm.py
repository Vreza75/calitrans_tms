# ai_core/llm.py

from __future__ import annotations
from ai_core.usage_logger import log_ai_usage

import json
import time
from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional

import streamlit as st
from openai import OpenAI


@dataclass
class LLMResult:
    ok: bool
    task: str
    model: str
    output_text: str
    output_json: Dict[str, Any]
    error: str
    latency_seconds: float
    attempts: int


class LLMWrapper:
    """
    Central OpenAI wrapper for CaliTrans AI agents.

    All agents should call this wrapper instead of calling OpenAI directly.
    """

    def __init__(self) -> None:
        self.api_key = st.secrets.get("OPENAI_API_KEY", "")
        self.default_model = st.secrets.get("OPENAI_RESPONSE_MODEL", "gpt-4.1-mini")

        if self.api_key:
            self.client = OpenAI(api_key=self.api_key)
        else:
            self.client = None

    def generate_json(
        self,
        *,
        task: str,
        system_prompt: str,
        user_payload: Dict[str, Any],
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_retries: int = 2,
    ) -> Dict[str, Any]:

        selected_model = model or self.default_model
        start = time.time()

        if not self.client:
            return asdict(LLMResult(
                ok=False,
                task=task,
                model=selected_model,
                output_text="",
                output_json={},
                error="OPENAI_API_KEY missing from Streamlit secrets.",
                latency_seconds=0,
                attempts=0,
            ))

        last_error = ""

        for attempt in range(1, max_retries + 2):
            try:
                response = self.client.responses.create(
                    model=selected_model,
                    input=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": json.dumps(user_payload, default=str)},
                    ],
                    temperature=temperature,
                )

                output_text = response.output_text.strip()
                output_json = self._safe_json_loads(output_text)

                usage = getattr(response, "usage", None)
                input_tokens = int(getattr(usage, "input_tokens", 0) or 0) if usage else 0
                output_tokens = int(getattr(usage, "output_tokens", 0) or 0) if usage else 0

                log_ai_usage(
                    task=task,
                    agent_name=task,
                    model=selected_model,
                    ok=True,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_seconds=round(time.time() - start, 3),
                    metadata={
                        "attempt": attempt,
                        "mode": "json",
                    },
                )

                return asdict(LLMResult(
                    ok=True,
                    task=task,
                    model=selected_model,
                    output_text=output_text,
                    output_json=output_json,
                    error="",
                    latency_seconds=round(time.time() - start, 3),
                    attempts=attempt,
                ))

            except Exception as exc:
                last_error = str(exc)
                time.sleep(0.75 * attempt)

        log_ai_usage(
            task=task,
            agent_name=task,
            model=selected_model,
            ok=False,
            input_tokens=0,
            output_tokens=0,
            latency_seconds=round(time.time() - start, 3),
            error=last_error,
            metadata={
                "attempts": max_retries + 1,
                "mode": "json",
            },
        )

        return asdict(LLMResult(
            ok=False,
            task=task,
            model=selected_model,
            output_text="",
            output_json={},
            error=last_error,
            latency_seconds=round(time.time() - start, 3),
            attempts=max_retries + 1,
        ))
    def generate_text(
         self,
        *,
        task: str,
        system_prompt: str,
        user_payload: Dict[str, Any],
        model: Optional[str] = None,
        temperature: float = 0.2,
        max_retries: int = 2,
    ) -> Dict[str, Any]:

        selected_model = model or self.default_model
        start = time.time()

        if not self.client:
            return asdict(LLMResult(
                ok=False,
                task=task,
                model=selected_model,
                output_text="",
                output_json={},
                error="OPENAI_API_KEY missing from Streamlit secrets.",
                latency_seconds=0,
                attempts=0,
            ))

        last_error = ""

        for attempt in range(1, max_retries + 2):
            try:
                response = self.client.responses.create(
                    model=selected_model,
                    input=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": json.dumps(user_payload, default=str)},
                    ],
                    temperature=temperature,
                )

                output_text = response.output_text.strip()
                output_json = self._safe_json_loads(output_text)

                usage = getattr(response, "usage", None)
                input_tokens = int(getattr(usage, "input_tokens", 0) or 0) if usage else 0
                output_tokens = int(getattr(usage, "output_tokens", 0) or 0) if usage else 0

                log_ai_usage(
                    task=task,
                    agent_name=task,
                    model=selected_model,
                    ok=True,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_seconds=round(time.time() - start, 3),
                    metadata={
                        "attempt": attempt,
                        "mode": "json",
                    },
                )

                return asdict(LLMResult(
                    ok=True,
                    task=task,
                    model=selected_model,
                    output_text=output_text,
                    output_json=output_json,
                    error="",
                    latency_seconds=round(time.time() - start, 3),
                    attempts=attempt,
                ))

            except Exception as exc:
                last_error = str(exc)
                time.sleep(0.75 * attempt)

        log_ai_usage(
            task=task,
            agent_name=task,
            model=selected_model,
            ok=False,
            input_tokens=0,
            output_tokens=0,
            latency_seconds=round(time.time() - start, 3),
            error=last_error,
            metadata={
                "attempts": max_retries + 1,
                "mode": "json",
            },
        )
        usage = getattr(response, "usage", None)
        input_tokens = int(getattr(usage, "input_tokens", 0) or 0) if usage else 0
        output_tokens = int(getattr(usage, "output_tokens", 0) or 0) if usage else 0

        log_ai_usage(
            task=task,
            agent_name=task,
            model=selected_model,
            ok=True,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_seconds=round(time.time() - start, 3),
            metadata={
                "attempt": attempt,
                "mode": "text",
            },
        )
        return asdict(LLMResult(
            ok=True,
            task=task,
            model=selected_model,
            output_text=output_text,
            output_json={},
            error="",
            latency_seconds=round(time.time() - start, 3),
            attempts=attempt,
        ))

    def _safe_json_loads(self, text: str) -> Dict[str, Any]:
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {"value": parsed}
        except Exception:
            return {
                "raw_text": text,
                "json_parse_error": True,
            }


@st.cache_resource(show_spinner=False)
def get_llm() -> LLMWrapper:
    return LLMWrapper()