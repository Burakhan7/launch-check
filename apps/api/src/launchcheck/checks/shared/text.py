"""Metin sadeleştirme yardımcıları (collector ve check'ler ortak kullanır; bağımlılıksız)."""

import re

_TR_FOLD = str.maketrans("ıİşŞğĞüÜöÖçÇâÂîÎûÛ", "iissgguuooccaaiiuu")
_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SPACES = re.compile(r"\s+")


def fold(text: str) -> str:
    """Türkçe karakterleri sadeleştirip küçük harfe çevirir: 'Adınız' → 'adiniz'."""
    return text.translate(_TR_FOLD).lower()


def tokens(text: str) -> list[str]:
    """camelCase ve ayraçlardan bölünmüş, sadeleştirilmiş kelimeler."""
    return [t for t in _TOKEN_SPLIT.split(fold(_CAMEL.sub(" ", text))) if t]


def squash(text: str) -> str:
    """Boşlukları teke indirir."""
    return _SPACES.sub(" ", text).strip()


def phrase_regex(phrases: tuple[str, ...] | list[str], prefix: bool = False) -> re.Pattern[str]:
    """Sadeleştirilmiş metinde kelime sınırlı eşleşme. `prefix=True`: Türkçe ekler serbest
    ('depresyon' → 'depresyondayim')."""
    tail = "" if prefix else r"(?![a-z0-9])"
    body = "|".join(re.escape(p) for p in sorted(phrases, key=len, reverse=True))
    return re.compile(rf"(?<![a-z0-9])(?:{body}){tail}")
