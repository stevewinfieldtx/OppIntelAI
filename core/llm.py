"""
OpenRouter LLM Client
Handles all communication with OpenRouter API including web search.
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
    Attempt to repair common JSON issues from LLM output.
    Handles: trailing commas, missing commas between properties,
    single quotes, unescaped newlines in strings, truncated output.
    """
    # Remove trailing commas before } or ]
    text = re.sub(r',\s*([}\]])', r'\1', text)

    # Fix missing commas between properties: }\n{ or "\n"
    # Pattern: end of value followed by newline and start of new key
    text = re.sub(r'"\s*\n\s*"', '",\n"', text)

    # Fix missing commas between array elements: }\n{
    text = re.sub(r'}\s*\n\s*{', '},\n{', text)

    # Fix missing commas after ] or } followed by "
    text = re.sub(r'([\]}])\s*\n\s*"', r'\1,\n"', text)

    # If JSON is truncated (no closing }), try to close it
    # Count unmatched braces
    open_braces = text.count('{') - text.count('}')
    open_brackets = text.count('[') - text.count(']')

    if open_braces > 0 or open_brackets > 0:
        # Try to find a reasonable truncation point
        # Remove the last incomplete property if we can
        last_comma = text.rfind(',')
        if last_comma > len(text) * 0.8:  # Only if near the end
            text = text[:last_comma]

        # Close any open structures
        text += ']' * open_brackets
        text += '}' * open_braces

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
    }

    if response_format:
        payload["response_format"] = response_format

    logger.info(
        f">>> OpenRouter REQUEST | model={request_model} | "
        f"web_search={use_web_search} | "
        f"temp={temperature} | max_tokens={max_tokens}"
    )

    async with httpx.AsyncClient(timeout=180.0) as client:
        try:
            response = await client.post(
                OPENROUTER_BASE_URL,
                headers=headers,
                json=payload,
            )

            logger.info(f"<<< OpenRouter RESPONSE | status={response.status_code}")

            if response.status_code != 200:
                error_body = response.text
                logger.error(
                    f"OpenRouter ERROR {response.status_code} | "
                    f"model={request_model} | body={error_body}"
                )
                response.raise_for_status()

            data = response.json()

            choice = data.get("choices", [{}])[0]
            message = choice.get("message", {})
            raw_content = message.get("content", "")
            content = _strip_thinking(raw_content)
            annotations = message.get("annotations", [])

            result = {
                "content": content,
                "content_raw": raw_content,
                "model": data.get("model", request_model),
                "usage": data.get("usage", {}),
                "annotations": annotations,
                "citations": [
                    a.get("url_citation", {}).get("url", "")
                    for a in annotations
                    if a.get("type") == "url_citation"
                ],
            }

            logger.info(
                f"<<< OpenRouter OK | model={result['model']} | "
                f"tokens_in={result['usage'].get('prompt_tokens', '?')} | "
                f"tokens_out={result['usage'].get('completion_tokens', '?')} | "
                f"citations={len(result['citations'])}"
            )

            return result

        except httpx.HTTPStatusError as e:
            logger.error(f"OpenRouter HTTPStatusError: {e.response.status_code} - {e.response.text}")
            raise
        except Exception as e:
            logger.error(f"OpenRouter call failed: {type(e).__name__}: {e}")
            raise


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
    Call LLM and parse response as JSON.
    Includes JSON repair and retry logic for resilience.
    """
    last_error = None

    for attempt in range(max_retries + 1):
        try:
            result = await call_llm(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                model=model,
                temperature=temperature + (attempt * 0.1),  # Slightly increase randomness on retry
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
                    raise ValueError(f"LLM returned invalid JSON after {max_retries + 1} attempts: {e}")

        except ValueError:
            raise
        except Exception as e:
            if attempt < max_retries:
                logger.warning(f"LLM call failed on attempt {attempt + 1}, retrying... Error: {e}")
                continue
            raise

    raise ValueError(f"LLM JSON call failed after all retries: {last_error}")
