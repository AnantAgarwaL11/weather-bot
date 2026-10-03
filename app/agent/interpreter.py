"""Query understanding: deterministic rules first, optional constrained LLM second.

The interpreter only extracts *what the user is asking about* (activity, groups,
location, time window). It never sees SOP conditions and cannot select a policy.
Any LLM output is validated against the vocabularies defined in the policy file.
"""
from __future__ import annotations

import difflib
import logging
import re

from pydantic import BaseModel

from app.agent.llm import LLMClient
from app.agent.prompts import INTENT_SYSTEM_PROMPT, intent_user_prompt
from app.policies.loader import PolicySet
from app.weather.client import TIME_WINDOWS

log = logging.getLogger(__name__)


class Intent(BaseModel):
    activity: str | None = None
    groups: list[str] = []
    location: str | None = None
    time_window: str | None = None  # TIME_WINDOWS key or "unsupported"
    followup: bool = False
    injection_suspected: bool = False
    source: str = "rules"


TIME_PATTERNS: list[tuple[str, str]] = [
    (r"\btomorrow\s+morning\b", "tomorrow_morning"),
    (r"\btomorrow\s+afternoon\b", "tomorrow_afternoon"),
    (r"\btomorrow\s+(evening|night)\b", "tomorrow_evening"),
    (r"\b(next week|weekend|monday|tuesday|wednesday|thursday|friday|saturday|sunday|day after tomorrow|in \d+ days)\b", "unsupported"),
    (r"\btomorrow\b", "tomorrow"),
    (r"\btonight\b|\blate night\b", "tonight"),
    (r"\bevening\b|\bafter work\b|\bsunset\b", "this_evening"),
    (r"\bafternoon\b|\bafter lunch\b", "this_afternoon"),
    (r"\bmorning\b", "this_morning"),
    (r"\b(right now|now|currently|at the moment|immediately)\b", "now"),
    (r"\btoday\b|\bthis weather\b", "today"),
]
INJECTION_PATTERNS = re.compile(
    r"ignore (all |any |your |the |previous )*(policies|policy|instructions|rules|sops?)|pretend|"
    r"override|disregard|you are now|system prompt|jailbreak|there is an sop|new sop|act as",
    re.I,
)
FOLLOWUP_CUES = re.compile(r"\b(what about|how about|instead|and if|then|same|there|that time|also)\b|^\s*and\b", re.I)
STOPWORDS = set(
    """a an the i me my we our us you your it its is are was be will would can could should shall may might do does
    did to for of on in at by with from about what how when where which who safe safely ok okay okey good bad idea fine
    go going get take taking bring this that these those there here today tomorrow tonight now morning afternoon
    evening night time day weather outside out conditions condition please tell know like want plan planning if
    any some and or but so just still also instead then same think suitable alright allowed recommended advisable
    let lets let's work office school home nice better best right currently moment""".split()
)
LOCATION_STOP = STOPWORDS | {"rain", "rainy", "sun", "sunny", "heat", "cold", "wind", "storm", "city", "town", "park",
                             "playground", "garden", "office", "work", "school", "commute", "college", "market"}
# Lookahead so overlapping candidates are all found ("to cycle in Bhopal" -> also "in Bhopal").
LOCATION_RE = re.compile(r"(?=\b(?:in|at|near|around|for|to|from|of)\s+([A-Za-z][A-Za-z.'\- ]*(?:,\s*[A-Za-z][A-Za-z.'\- ]*)?))", re.I)
WHAT_ABOUT_RE = re.compile(r"\b(?:what|how)\s+about\s+([A-Za-z][A-Za-z.'\- ]*(?:,\s*[A-Za-z][A-Za-z.'\- ]*)?)", re.I)


def _normalize(text: str) -> str:
    return re.sub(r"[^a-z0-9,'\- ]+", " ", text.lower()).replace("-", " ")


def _ngrams(tokens: list[str], n_max: int = 3) -> list[str]:
    return [" ".join(tokens[i:i + n]) for n in range(1, n_max + 1) for i in range(len(tokens) - n + 1)]


def _vocab_hits(text: str, vocab: dict) -> tuple[list[str], set[str]]:
    """Return vocab keys mentioned (in vocab order) and the tokens they consumed.
    Exact n-gram match first, then fuzzy single-token match for typos (e.g. 'bicyle')."""
    tokens = [t.strip(",'") for t in _normalize(text).split() if t.strip(",'")]
    grams = set(_ngrams(tokens))
    hits, used = [], set()
    for key, v in vocab.items():
        syns = {s.lower().replace("-", " ") for s in v.synonyms} | {key.replace("_", " ")}
        found = grams & syns
        if not found:
            singles = [s for s in syns if " " not in s and len(s) >= 5]
            found = {t for t in tokens if len(t) >= 5 and difflib.get_close_matches(t, singles, n=1, cutoff=0.85)}
        if found:
            hits.append(key)
            used |= {w for g in found for w in g.split()}
    return hits, used


def _time_window(text: str) -> str | None:
    low = text.lower()
    for pattern, key in TIME_PATTERNS:
        if re.search(pattern, low):
            return key
    return None


def _clean_location(raw: str, vocab_words: set[str]) -> str | None:
    if "," in raw:
        city, _, qual = raw.partition(",")
        city_part, qual_part = _clean_location(city, vocab_words), _clean_location(qual, vocab_words)
        return f"{city_part}, {qual_part}" if city_part and qual_part else city_part
    words = []
    for w in raw.strip().split():
        wl = w.lower().strip(".'")
        if wl in LOCATION_STOP or wl in vocab_words or not wl.replace(".", "").isalpha():
            break
        words.append(w.strip())
        if len(words) == 4:
            break
    return " ".join(words) if words else None


def _location(text: str, vocab_words: set[str]) -> str | None:
    candidates = [m.group(1) for m in WHAT_ABOUT_RE.finditer(text)] + [m.group(1) for m in LOCATION_RE.finditer(text)]
    cleaned = [c for c in (_clean_location(c, vocab_words) for c in candidates) if c]
    capitalized = [c for c in cleaned if c[0].isupper()]
    chosen = (capitalized or cleaned or [None])[0]
    return chosen.title() if chosen and chosen.islower() else chosen


class Interpreter:
    def __init__(self, policies_fn, llm: LLMClient | None = None):
        self._policies_fn = policies_fn  # callable -> current PolicySet (supports hot reload)
        self.llm = llm

    def interpret(self, message: str, context: dict) -> Intent:
        policies: PolicySet = self._policies_fn()
        intent = self.rules(message, policies)
        if self.llm is not None:
            intent = self._merge_llm(message, context, intent, policies)
        return intent

    @staticmethod
    def rules(message: str, policies: PolicySet) -> Intent:
        activities, used_a = _vocab_hits(message, policies.activities)
        groups, used_g = _vocab_hits(message, policies.groups)
        vocab_words = {w for v in list(policies.activities.values()) + list(policies.groups.values())
                       for s in v.synonyms for w in s.lower().replace("-", " ").split()}
        location = _location(message, vocab_words)
        time_window = _time_window(message)
        tokens = {t.strip(",'") for t in _normalize(message).split()}
        loc_words = {w.lower() for w in (location or "").replace(",", " ").split()}
        unknown = {t for t in tokens if t and t not in STOPWORDS and t not in used_a | used_g | loc_words and not t.isdigit()}
        followup = bool(FOLLOWUP_CUES.search(message)) or (not activities and not unknown)
        return Intent(
            activity=activities[0] if activities else None,
            groups=groups,
            location=location,
            time_window=time_window,
            followup=followup,
            injection_suspected=bool(INJECTION_PATTERNS.search(message)),
        )

    def _merge_llm(self, message: str, context: dict, intent: Intent, policies: PolicySet) -> Intent:
        try:
            raw = self.llm.complete_json(
                INTENT_SYSTEM_PROMPT,
                intent_user_prompt(message, context, policies, list(TIME_WINDOWS)),
            )
        except Exception as exc:  # LLM is optional; rules result stands
            log.warning("LLM intent extraction failed, using rules: %s", exc)
            return intent
        llm_activity = raw.get("activity")
        llm_groups = raw.get("groups") or []
        llm_time = raw.get("time_window")
        llm_loc = raw.get("location")
        update: dict = {"source": "rules+llm"}
        # Only accept values from the closed vocabularies; anything else is discarded.
        if llm_activity in policies.activities:
            update["activity"] = llm_activity
        elif llm_activity is not None:
            log.info("discarding LLM activity outside vocabulary: %r", llm_activity)
        if isinstance(llm_groups, list):
            update["groups"] = sorted(set(intent.groups) | {g for g in llm_groups if g in policies.groups})
        if intent.time_window is None and llm_time in TIME_WINDOWS:
            update["time_window"] = llm_time
        if intent.location is None and isinstance(llm_loc, str) and 0 < len(llm_loc) <= 60:
            update["location"] = llm_loc.strip()
        if isinstance(raw.get("is_followup"), bool) and not intent.activity:
            update["followup"] = raw["is_followup"]
        return intent.model_copy(update=update)
