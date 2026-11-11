"""Cliente para /api/chat: DeepSeek primero, Groq de respaldo.

El resto de los módulos conserva el cliente Groq original.
"""
import logging
import os

log = logging.getLogger(__name__)


class _Completions:
    def __init__(self, parent):
        self.parent = parent

    def create(self, **kwargs):
        primary = self.parent.deepseek
        if primary is not None:
            from openai import APIConnectionError, APITimeoutError, APIStatusError, RateLimitError
            try:
                return primary.chat.completions.create(
                    **{**kwargs, "model": self.parent.deepseek_model}
                )
            except (APIConnectionError, APITimeoutError, RateLimitError, APIStatusError) as exc:
                # Credenciales inválidas (401/403) o solicitud inválida (400/422)
                # no son fallos de saldo/cuota/disponibilidad: exponer el error.
                status = getattr(exc, "status_code", None)
                if status not in (None, 402, 408, 429, 500, 502, 503, 504):
                    raise
                if self.parent.groq is None:
                    raise
                log.warning("DeepSeek no disponible (HTTP %s); usando Groq para esta consulta", status)
        if self.parent.groq is None:
            raise RuntimeError("No hay proveedor de chat disponible")
        return self.parent.groq.chat.completions.create(**kwargs)


class _Chat:
    def __init__(self, parent):
        self.completions = _Completions(parent)


class ClienteChatConFallback:
    def __init__(self, groq_client=None):
        self.groq = groq_client
        self.deepseek = None
        self.deepseek_model = os.getenv("DEEPSEEK_MODEL") or "deepseek-chat"
        key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if key and not key.lower().startswith(("tu_clave", "your_key")):
            from openai import OpenAI
            self.deepseek = OpenAI(
                api_key=key,
                base_url="https://api.deepseek.com",
                timeout=45.0,
                max_retries=0,
            )
        self.chat = _Chat(self)

    @property
    def disponible(self):
        return self.deepseek is not None or self.groq is not None
