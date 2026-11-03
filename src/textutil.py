"""Small text helpers: pulling the [emotion] tag off a reply, cutting a stream into sentences,
and fuzzy-matching the wake phrase."""
import re
from difflib import SequenceMatcher

from . import config

EMOTIONS = {"happy", "relaxed", "sad", "angry", "surprised", "neutral"}
_TAG = re.compile(r"\[\s*(\w+)\s*\]")
_SENTENCE_END = re.compile(r"[.!?…]+[\"')\]]*\s")


def clean_for_speech(s):
    s = re.sub(r"\*[^*]*\*", "", s)                     # *actions*
    s = _TAG.sub("", s)                                  # stray [tags]
    s = re.sub(r"[`#*_~>|]", "", s)                      # markdown leftovers
    s = re.sub(r"[\U0001F300-\U0001FAFF☀-➿]", "", s)   # emoji
    return re.sub(r"\s+", " ", s).strip()


class SentenceStream:
    """Feed it the raw chunks from the LLM, get clean sentences back as they complete.
    The leading [emotion] tag is pulled out into `.emotion`."""

    def __init__(self):
        self.buf = ""
        self.emotion = None
        self._tag_done = False

    def feed(self, chunk):
        self.buf += chunk
        if not self._tag_done:
            text = self.buf.lstrip()
            if not text:
                return []
            if text.startswith("["):
                end = text.find("]")
                if end == -1:
                    if len(text) <= 16:
                        return []                   # the tag is probably still arriving
                    self._tag_done = True
                else:
                    name = text[1:end].strip().lower()
                    self.emotion = name if name in EMOTIONS else None
                    self.buf = text[end + 1:]
                    self._tag_done = True
            else:
                self._tag_done = True
        return self._cut()

    def flush(self):
        out = self._cut()
        rest = clean_for_speech(self.buf)
        self.buf = ""
        if rest:
            out.append(rest)
        return out

    def _cut(self):
        out = []
        start = 0
        for m in _SENTENCE_END.finditer(self.buf):
            if m.end() - start >= config.MIN_SENTENCE_CHARS:
                sentence = clean_for_speech(self.buf[start:m.end()])
                if sentence:
                    out.append(sentence)
                start = m.end()
        self.buf = self.buf[start:]
        return out


def _words(text):
    return re.sub(r"[^a-z0-9' ]", " ", text.lower()).split()


def match_wake(text):
    """Look for a wake phrase anywhere in `text`. Returns (found, whatever came after it)."""
    words = _words(text)
    best_score, best_end = 0.0, -1
    for phrase in config.WAKE_PHRASES:
        target = " ".join(_words(phrase))
        n = len(target.split())
        for size in (n - 1, n, n + 1):
            if size < 1:
                continue
            for i in range(len(words) - size + 1):
                score = SequenceMatcher(None, " ".join(words[i:i + size]), target).ratio()
                if score > best_score:
                    best_score, best_end = score, i + size
    if best_score >= config.WAKE_MATCH_THRESHOLD:
        return True, " ".join(words[best_end:])
    return False, text.strip()


def strip_leading_wake(text):
    """Whisper keeps the wake phrase in the transcript, so cut it back off."""
    if not match_wake(" ".join(text.split()[:5]))[0]:
        return text.strip()
    words = text.split()
    for cut in range(1, min(6, len(words)) + 1):
        if match_wake(" ".join(words[:cut]))[0]:
            return " ".join(words[cut:]).strip(" ,.!?")
    return text.strip()
