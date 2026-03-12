from __future__ import annotations

import re


LANGUAGE_ALIASES = {
    "en": "en",
    "english": "en",
    "ko": "ko",
    "kr": "ko",
    "korean": "ko",
    "ja": "ja",
    "jp": "ja",
    "japanese": "ja",
    "zh": "zh",
    "cn": "zh",
    "chinese": "zh",
}

LANGUAGE_NAMES = {
    "en": "English",
    "ko": "Korean",
    "ja": "Japanese",
    "zh": "Chinese",
}

RUNTIME_LANGUAGE_ALIASES = {
    "en": "EN",
    "english": "EN",
    "ko": "KO",
    "kr": "KO",
    "korean": "KO",
}


def normalize_language(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = LANGUAGE_ALIASES.get(value.strip().lower())
    return normalized


def language_name(code: str | None) -> str:
    normalized = normalize_language(code) or "en"
    return LANGUAGE_NAMES.get(normalized, "English")


def normalize_runtime_language(value: str | None) -> str | None:
    if value is None:
        return None
    return RUNTIME_LANGUAGE_ALIASES.get(value.strip().lower())


def runtime_language_name(value: str | None) -> str:
    normalized = normalize_runtime_language(value) or "EN"
    return "Korean" if normalized == "KO" else "English"


def runtime_language_code_to_request_language(value: str | None) -> str:
    normalized = normalize_runtime_language(value) or "EN"
    return normalized.lower()


def detect_primary_language(text: str) -> str:
    counts = {
        "ko": len(re.findall(r"[\uac00-\ud7a3]", text)),
        "ja": len(re.findall(r"[\u3040-\u30ff]", text)),
        "zh": len(re.findall(r"[\u4e00-\u9fff]", text)),
        "en": len(re.findall(r"[A-Za-z]", text)),
    }
    if counts["ko"]:
        return "ko"
    if counts["ja"]:
        return "ja"
    if counts["zh"] and counts["zh"] >= counts["en"]:
        return "zh"
    return "en"
