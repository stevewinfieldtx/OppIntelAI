"""
OpenRouter LLM Client
Handles all communication with OpenRouter API including web search.
Uses streaming for all LLM calls to avoid truncation on large outputs.
Web search uses the :online model suffix for maximum compatibility.
"""
import httpx
import json
import re
import logging
from typing import Optional
from core.config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, LLM_MODEL

logger = logging.getLogger(__name__)


def _strip_thinking(text: str) -> str:
    """Strip <think>...</think> blocks from reasoning model output."""
    cleaned = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL)
    return cleaned.strip()


def _strip_code_fences(text: str) -> str:
    """Strip markdown code fences from LLM output."""
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _repair_json(text: str) -> str:
    """
    Repair truncated or malformed JSON from LLM output.

    Uses a stack to track open structures so closing brackets/braces are
    inserted in the correct order. Also handles:
    - Unterminated strings (the most common truncation failure)
    - Trailing commas
    - Missing commas between properties
    """
    # ── pre-clean ──────────────────────────────────────────────────────────
    # Remove trailing commas before } or ]
    text = re.sub(r',\s*([}\]])', r'\1', text)
    # Fix missing commas between adjacent quoted strings on separate lines
    text = re.sub(r'"\s*\n\s*"', '",\n"', text)
    # Fix missing commas between adjacent objects in an array
    text = re.sub(r'}\s*\n\s*{', '},\n{', text)
    # Fix missing commas after ] or } before a new key
    text = re.sub(r'([\]}])\s*\n\s*"', r'\1,\n"', text)

    # ── stack-based structural analysis ────────────────────────────────────
    stack = []       # tracks '{' and '[' that are open
    in_string = False
    escaped = False

    for ch in text:
        if escaped:
            escaped = False
            continue
        if ch == '\\' and in_string:
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if not in_string:
            if ch in ('{', '['):
                stack.append(ch)
            elif ch == '}' and stack and stack[-1] == '{':
                stack.pop()
            elif ch == ']' and stack and stack[-1] == '[':
                stack.pop()

    # Nothing open, nothing in a string → text is structurally complete
    if not stack and not in_string:
        return text

    # ── close unterminated string ───────────────────────────────────────────
    if in_string:
        text = text.rstrip()
        if text.endswith('\\'):
            text = text[:-1]
        text += '"'

    # ── strip dangling incomplete key or opener ─────────────────────────────
    # e.g. `..."key":` or `..."key": {` at the very end with no value
    text = re.sub(r',?\s*"[^"]*"\s*:\s*[\[{]?\s*$', '', text)
    # e.g. a just-closed string that has no colon after it (orphaned key)
    text = re.sub(r',\s*"[^"]*"\s*$', '', text)
    # Strip orphaned trailing comma
    text = text.rstrip().rstrip(',')

    # ── close open structures in reverse stack order ───────────────────────
    # Re-scan after cleanup in case we removed some openers
    stack2 = []
    in_str2 = False
    esc2 = False
    for ch in text:
        if esc2:
            esc2 = False
            continue
        if ch == '\\' and in_str2:
            esc2 = True
            continue
        if ch == '"':
            in_str2 = not in_str2
            continue
        if not in_str2:
            if ch in ('{', '['):
                stack2.append(ch)
            elif ch == '}' and stack2 and stack2[-1] == '{':
                stack2.pop()
            elif ch == ']' and stack2 and stack2[-1] == '[':
                stack2.pop()

    closer = {'{': '}', '[': ']'}
    for opener in reversed(stack2):
        text += closer[opener]

    return text


async def call_llm(
    system_prompt: str,
    user_prompt: str,
    model: str = None,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    use_web_search: bool = False,
    response_format: Optional[dict] = None,
) -> dict:
    model = model or LLM_MODEL

    # Append :online for web search
    request_model = model
    if use_web_search and ":online" not in model:
        request_model = f"{model}:online"

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://oppintelai.up.railway.app",
        "X-Title": "OppIntelAI",
    }

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    payload = {
        "model": request_model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }

    if response_format:
        payload["response_format"] = response_format

    logger.info(
        f">>> OpenRouter REQUEST (streaming) | model={request_model} | "
        f"web_search={use_web_search} | "
        f"temp={temperature} | max_tokens={max_tokens}"
    )

    chunks = []
    usage = {}
    model_used = request_model
    annotations = []

    async with httpx.AsyncClient(timeout=300.0) as client:
        try:
            async with client.stream(
                "POST",
                OPENROUTER_BASE_URL,
                headers=headers,
                json=payload,
            ) as response:
                logger.info(f"<<< OpenRouter STREAM started | status={response.status_code}")

                if response.status_code != 200:
                    error_body = await response.aread()
                    error_text = error_body.decode("utf-8", errors="replace")
                    logger.error(
                        f"OpenRouter ERROR {response.status_code} | "
                        f"model={request_model} | body={error_text}"
                    )
                    response.raise_for_status()

                async for line in response.aiter_lines():
                    if not line:
                        continue
                    if line.startswith("data: "):
                        line = line[6:]
                    if line == "[DONE]":
                        break
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Capture usage from the final chunk (some providers send it here)
                    if "usage" in event and event["usage"]:
                        usage = event["usage"]

                    if "model" in event and event["model"]:
                        model_used = event["model"]

                    choice = (event.get("choices") or [{}])[0]
                    delta = choice.get("delta", {})
                    token = delta.get("content")
                    if token:
                        chunks.append(token)

                    # Capture annotations if present (web search citations)
                    for ann in delta.get("annotations", []):
                        annotations.append(ann)

        except httpx.HTTPStatusError as e:
            logger.error(f"OpenRouter HTTPStatusError: {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"OpenRouter stream failed: {type(e).__name__}: {e}")
            raise

    raw_content = "".join(chunks)
    content = _strip_thinking(raw_content)

    logger.info(
        f"<<< OpenRouter STREAM complete | model={model_used} | "
        f"tokens_in={usage.get('prompt_tokens', '?')} | "
        f"tokens_out={usage.get('completion_tokens', '?')} | "
        f"total_chars={len(raw_content)} | "
        f"citations={len([a for a in annotations if a.get('type') == 'url_citation'])}"
    )

    return {
        "content": content,
        "content_raw": raw_content,
        "model": model_used,
        "usage": usage,
        "annotations": annotations,
        "citations": [
            a.get("url_citation", {}).get("url", "")
            for a in annotations
            if a.get("type") == "url_citation"
        ],
    }


async def call_llm_json(
    system_prompt: str,
    user_prompt: str,
    model: str = None,
    temperature: float = 0.2,
    max_tokens: int = 4096,
    use_web_search: bool = False,
    max_retries: int = 2,
) -> dict:
    """
    Call LLM via streaming and parse response as JSON.
    Streaming ensures we receive the full response even for large outputs.
    Includes JSON repair and retry logic for resilience.
    """
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            result = await call_llm(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model=model,
                temperature=temperature + (attempt * 0.1),
                max_tokens=max_tokens,
                use_web_search=use_web_search,
            )

            content = result["content"]
            content = _strip_code_fences(content)

            # Extract JSON object from surrounding text
            json_start = content.find('{')
            json_end = content.rfind('}')
            if json_start != -1 and json_end != -1 and json_end > json_start:
                content = content[json_start:json_end + 1]

            # First try: parse as-is
            try:
                parsed = json.loads(content)
                result["parsed"] = parsed
                return result
            except json.JSONDecodeError:
                pass

            # Second try: repair and parse
            repaired = _repair_json(content)
            try:
                parsed = json.loads(repaired)
                logger.info(f"JSON repaired successfully on attempt {attempt + 1}")
                result["parsed"] = parsed
                return result
            except json.JSONDecodeError as e:
                last_error = e
                if attempt < max_retries:
                    logger.warning(
                        f"JSON parse failed on attempt {attempt + 1}, retrying... "
                        f"Error: {e}"
                    )
                    continue
                else:
                    logger.error(
                        f"JSON parse failed after {max_retries + 1} attempts: {e}\n"
                        f"Content (first 500 chars): {content[:500]}\n"
                        f"Content (last 500 chars): {content[-500:]}"
                    )
                    raise ValueError(
                        f"LLM returned invalid JSON after {max_retries + 1} attempts: {e}"
                    )

        except ValueError:
            raise
        except Exception as e:
            if attempt < max_retries:
                logger.warning(f"LLM call failed on attempt {attempt + 1}, retrying... Error: {e}")
                continue
            raise

    raise ValueError(f"LLM JSON call failed after all retries: {last_error}")
