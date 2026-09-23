"""Optional remote AI: semantic scoring, source-quote selection and audit.

The rule engine owns eligibility. This module never changes profile facts.
"""

import hashlib
import json
import math
import os
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request as HttpRequest, urlopen

from .data import key


ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".cache" / "embeddings.json"
EVIDENCE_CACHE = ROOT / ".cache" / "evidence.json"
OPENAI_BASE = "https://api.openai.com/v1"
NVIDIA_BASE = "https://integrate.api.nvidia.com/v1"
QUOTE_SCHEMA = {
    "type": "object", "properties": {
        "items": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string"}, "quote": {"type": "string"}},
            "required": ["id", "quote"], "additionalProperties": False}}},
    "required": ["items"], "additionalProperties": False,
}
AUDIT_SCHEMA = {
    "type": "object", "properties": {"approved_ids": {
        "type": "array", "items": {"type": "string"}}},
    "required": ["approved_ids"], "additionalProperties": False,
}


class AIUnavailable(Exception):
    """Safe-to-display provider error with no key or response body."""


def configuration():
    """Environment takes precedence; read either repo .env or existing tests/.env."""
    values = {}
    for path in (ROOT / "tests" / ".env", ROOT / ".env"):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            name = name.strip()
            if name in {"OPENAI_API_KEY", "NVIDIA_API_KEY", "OPENAI_EMBEDDING_MODEL",
                        "OPENAI_CHAT_MODEL", "NVIDIA_CHAT_MODEL"}:
                values[name] = value.strip().strip('"\'')
    for name in ("OPENAI_API_KEY", "NVIDIA_API_KEY", "OPENAI_EMBEDDING_MODEL",
                 "OPENAI_CHAT_MODEL", "NVIDIA_CHAT_MODEL"):
        if os.environ.get(name):
            values[name] = os.environ[name]
    return values


def _post(base, path, key, body, timeout=6):
    if not key:
        raise AIUnavailable("API-ключ не задан")
    request = HttpRequest(base + path, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                          headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                          method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except HTTPError as exc:
        raise AIUnavailable(f"API вернул HTTP {exc.code}") from None
    except (URLError, TimeoutError, OSError, ValueError):
        raise AIUnavailable("API временно недоступен") from None


def _json_content(response):
    try:
        content = response["choices"][0]["message"]["content"]
        if content.startswith("```"):
            content = content.split("\n", 1)[1].rsplit("```", 1)[0]
        return json.loads(content)
    except (KeyError, IndexError, TypeError, ValueError):
        raise AIUnavailable("API вернул ответ в неверном формате") from None


def _cosine(a, b):
    if len(a) != len(b) or not a:
        raise AIUnavailable("Размерность embeddings не совпадает")
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if not norm_a or not norm_b:
        raise AIUnavailable("Пустой embedding")
    return max(0.0, min(1.0, sum(x * y for x, y in zip(a, b)) / (norm_a * norm_b)))


class AIService:
    def __init__(self, config=None, cache_path=CACHE, evidence_cache_path=EVIDENCE_CACHE):
        self.config = configuration() if config is None else config
        self.cache_path = Path(cache_path)
        self.evidence_cache_path = Path(evidence_cache_path)
        try:
            self.cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.cache = {}
        try:
            self.evidence_cache = json.loads(self.evidence_cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.evidence_cache = {}

    @property
    def available(self):
        return bool(self.config.get("OPENAI_API_KEY") or self.config.get("NVIDIA_API_KEY"))

    def _embed(self, texts):
        model = self.config.get("OPENAI_EMBEDDING_MODEL", "text-embedding-3-small")
        fingerprints = [hashlib.sha256((model + "\0" + text).encode()).hexdigest() for text in texts]
        missing = [(fingerprint, text) for fingerprint, text in zip(fingerprints, texts)
                   if fingerprint not in self.cache]
        if missing:
            response = _post(OPENAI_BASE, "/embeddings", self.config.get("OPENAI_API_KEY"),
                             {"model": model, "input": [text for _, text in missing], "encoding_format": "float"})
            try:
                rows = sorted(response["data"], key=lambda row: row["index"])
                vectors = [row["embedding"] for row in rows]
                if len(vectors) != len(missing) or not all(v and all(math.isfinite(x) for x in v) for v in vectors):
                    raise ValueError("invalid embeddings")
            except (KeyError, TypeError, ValueError):
                raise AIUnavailable("API вернул неверные embeddings") from None
            self.cache.update({fingerprint: vector for (fingerprint, _), vector in zip(missing, vectors)})
            try:
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                self.cache_path.write_text(json.dumps(self.cache, separators=(",", ":")), encoding="utf-8")
            except OSError:
                pass  # Memory cache still works in read-only deployments.
        return [self.cache[fingerprint] for fingerprint in fingerprints]

    def semantic_scores(self, profiles, preference):
        """Only already eligible profiles are embedded. No preference means no AI score."""
        if not preference:
            return {}
        present = [p for p in profiles if p.description.strip()]
        if not present:
            return {p.id: 0.0 for p in profiles}
        vectors = self._embed([preference] + [p.description[:4000] for p in present])
        result = {p.id: _cosine(vectors[0], vector) for p, vector in zip(present, vectors[1:])}
        result.update({p.id: 0.0 for p in profiles if not p.description.strip()})
        return result

    def _chat(self, provider, system, payload, schema=None):
        if provider == "OpenAI":
            body = {"model": self.config.get("OPENAI_CHAT_MODEL", "gpt-4o-mini"),
                    "temperature": 0, "max_tokens": 800,
                    "messages": [{"role": "system", "content": system},
                                 {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]}
            if schema is not None:
                body["response_format"] = {"type": "json_schema", "json_schema": {
                    "name": "contractor_evidence", "strict": True, "schema": schema}}
            response = _post(OPENAI_BASE, "/chat/completions", self.config.get("OPENAI_API_KEY"), body)
        else:
            body = {"model": self.config.get("NVIDIA_CHAT_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b"),
                    "temperature": 0, "max_tokens": 800,
                    "messages": [{"role": "system", "content": system},
                                 {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]}
            response = _post(NVIDIA_BASE, "/chat/completions", self.config.get("NVIDIA_API_KEY"), body)
        return _json_content(response)

    def evidence(self, candidates, request):
        """Return audited, exact source quotes. Failure leaves template explanations intact."""
        records = [{"id": c["profile"].id, "description": c["profile"].description[:4000]}
                   for c in candidates if c["profile"].description]
        if not records:
            return {}, "Нет описаний для AI-проверки"
        context = {"category": request.category, "format": request.event_format,
                   "preference": request.preference, "profiles": records}
        fingerprint = hashlib.sha256(json.dumps({"context": context,
            "openai_model": self.config.get("OPENAI_CHAT_MODEL", "gpt-4o-mini"),
            "nvidia_model": self.config.get("NVIDIA_CHAT_MODEL", "nvidia/nemotron-3.5-lightning-30b-a3b")},
            ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        if fingerprint in self.evidence_cache:
            cached = self.evidence_cache[fingerprint]
            return cached["quotes"], cached["status"] + " (из кеша)"
        writer = "Выбери по одной дословной фразе из каждого description, прямо подтверждающей пожелание, " \
                 "а если пожелания нет — категорию или формат мероприятия. " \
                 "Не пересказывай и не добавляй факты. Верни JSON: items [{id, quote}]. " \
                 "Если подтверждающей фразы нет, quote должен быть пустым."
        try:
            proposed = self._chat("OpenAI", writer, context, QUOTE_SCHEMA)
            writer_name = "OpenAI"
        except AIUnavailable:
            try:
                proposed = self._chat("NVIDIA", writer, context)
                writer_name = "NVIDIA"
            except AIUnavailable as exc:
                return {}, f"AI недоступен ({exc}); показаны локальные объяснения"
        descriptions = {row["id"]: row["description"] for row in records}
        quotes = {}
        for item in proposed.get("items", []) if isinstance(proposed, dict) else []:
            if not isinstance(item, dict):
                continue
            pid, quote = item.get("id"), item.get("quote")
            if pid in descriptions and isinstance(quote, str) and 12 <= len(quote) <= 320 \
                    and quote in descriptions[pid]:
                if re.search(r"\bбез\b", key(request.preference)) and not re.search(r"\bбез\b", key(quote)):
                    continue
                if not request.preference and not any(key(target)[:5] in key(quote) for target in
                                                      (request.category, request.event_format)):
                    continue
                quotes[pid] = quote
        if not quotes:
            return {}, "AI не нашёл дословного подтверждения; показаны локальные объяснения"
        if writer_name == "NVIDIA":
            status = "NVIDIA: дословные фразы сверены с профилями"
            self._save_evidence(fingerprint, quotes, status)
            return quotes, status
        auditor = "Проверь, действительно ли каждая quote относится к preference, а если preference пустое — к category или format. " \
                  "Верни только JSON с полем approved_ids: список id подходящих цитат. При сомнении отклони."
        audit_payload = {"category": request.category, "format": request.event_format, "preference": request.preference,
                         "quotes": quotes}
        try:
            verdict = self._chat("NVIDIA", auditor, audit_payload)
            auditor_name = "NVIDIA"
        except AIUnavailable:
            try:
                verdict = self._chat("OpenAI", auditor, audit_payload, AUDIT_SCHEMA)
                auditor_name = "OpenAI"
            except AIUnavailable:
                return {}, "AI-аудитор недоступен; показаны локальные объяснения"
        try:
            approved = verdict.get("approved_ids", []) if isinstance(verdict, dict) else []
            if not isinstance(approved, list):
                raise AIUnavailable("неверный ответ аудитора")
            accepted = {pid: quote for pid, quote in quotes.items() if pid in approved}
            status = f"OpenAI предложил фразы; {auditor_name} проверил смысл; текст сверен с профилями"
            self._save_evidence(fingerprint, accepted, status)
            return accepted, status
        except AIUnavailable:
            return {}, "AI-аудитор недоступен; показаны локальные объяснения"

    def _save_evidence(self, fingerprint, quotes, status):
        self.evidence_cache[fingerprint] = {"quotes": quotes, "status": status}
        try:
            self.evidence_cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.evidence_cache_path.write_text(json.dumps(self.evidence_cache, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass
