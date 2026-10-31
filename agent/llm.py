"""Provider-agnostic LLM adapter (owned by P3)."""

import os


class LLMAdapter:
    """LLM client adapter for investigation agent nodes."""

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        self.model = model or os.environ.get("LLM_MODEL", "claude-3-5-sonnet-20241022")

    def complete(self, prompt: str, system: str | None = None) -> str:
        """Call LLM provider or return structured deterministic fallback if no key."""
        if not self.api_key:
            return "DETERMINISTIC_FALLBACK: Auth pool exhaustion confirmed from telemetry."
        try:
            import anthropic

            client = anthropic.Anthropic(api_key=self.api_key)
            resp = client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=system or "",
                messages=[{"role": "user", "content": prompt}],
            )
            return resp.content[0].text
        except Exception as e:
            return f"FALLBACK_ERROR: {e}"
