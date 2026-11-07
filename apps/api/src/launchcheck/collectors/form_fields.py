"""Form alanlarını "soru" gruplarına ayırır ve özel nitelikli kişisel veri kategorilerini bulur.

Bir grup = tek bir soru: select, aynı `name`'li radio/checkbox'lar veya tek bir metin alanı.
Grubun metni: name/id/placeholder/aria-label, bağlı label'lar, fieldset legend'ı, seçenekler ve
sorunun yakınındaki kısa metin. Alan DEĞERLERİ okunmaz/saklanmaz.
"""

from dataclasses import dataclass, field
from typing import Any

from selectolax.lexbor import LexborHTMLParser, LexborNode

from launchcheck.checks.shared import keywords
from launchcheck.checks.shared.text import fold, squash

SKIPPED_TYPES = {"hidden", "submit", "button", "reset", "image", "search", "password"}
QUESTION_LEVELS = 1  # sorunun metni için alanın kaç üst kapsayıcısına bakılır (label/legend ayrıca)
MAX_QUESTION_CHARS = 300
MIN_CHOICES = 2
SPECIAL_PATH = "special_categories"
CHOICE_SELECTOR = "button, [role=radio], [role=option], [aria-checked]"
CHOICE_ROLES = {"radio", "option", "checkbox", "switch"}
MAX_CHOICE_BUTTONS = 30  # daha fazlası bir soru değil, düğme listesi/araç çubuğudur


def _attr(node: LexborNode, name: str) -> str:
    value: Any = node.attributes.get(name)
    return str(value) if value else ""


def _ancestor(node: LexborNode, levels: int) -> LexborNode:
    current = node
    for _ in range(levels):
        parent = current.parent
        if parent is None or parent.tag in ("html", "body", "form", None):
            break
        current = parent
    return current


def _own_texts(node: LexborNode, labels: dict[str, str]) -> list[str]:
    texts = [_attr(node, a) for a in ("name", "id", "placeholder", "aria-label", "title")]
    texts.append(labels.get(_attr(node, "id"), ""))
    current = node.parent
    while current is not None and current.tag not in ("form", "body"):
        if current.tag == "label":
            texts.append(current.text(deep=True, separator=" "))
        if current.tag == "fieldset":
            legend = current.css_first("legend")
            if legend is not None:
                texts.append(legend.text(deep=True, separator=" "))
            break
        current = current.parent
    return texts


@dataclass
class FieldGroup:
    node: LexborNode  # grubun ilk alanı (kapsayıcı aramak için)
    text: str  # sadeleştirilmiş birleşik metin
    choice: bool  # select veya en az 2 seçenekli radio/checkbox grubu
    categories: dict[str, set[str]] = field(default_factory=dict)


def field_groups(tree: LexborHTMLParser) -> list[FieldGroup]:
    labels = {_attr(n, "for"): n.text(deep=True, separator=" ") for n in tree.css("label[for]")}
    grouped: dict[str, list[LexborNode]] = {}
    singles: list[LexborNode] = []
    for node in tree.css("input, select, textarea"):
        input_type = _attr(node, "type").lower()
        if node.tag == "input" and input_type in SKIPPED_TYPES:
            continue
        if node.tag == "input" and input_type in ("radio", "checkbox") and _attr(node, "name"):
            grouped.setdefault(_attr(node, "name"), []).append(node)
        else:
            singles.append(node)
    groups = []
    for node in singles:
        texts = _own_texts(node, labels)
        choice = node.tag == "select"
        if choice:
            texts += [o.text(deep=True) for o in node.css("option")]
        groups.append(_group(node, texts, choice))
    for nodes in grouped.values():
        texts = [t for n in nodes for t in _own_texts(n, labels)]
        groups.append(_group(nodes[0], texts, len(nodes) >= MIN_CHOICES))
    groups += _button_choice_groups(tree)
    return groups


def _is_choice_button(node: LexborNode) -> bool:
    if node.tag == "button":
        return _attr(node, "type").lower() == "button" or _attr(node, "aria-pressed") != ""
    return _attr(node, "role") in CHOICE_ROLES or bool(_attr(node, "aria-checked"))


def _in_chrome(node: LexborNode) -> bool:
    """Menü, üst bilgi ve footer'daki düğmeler soru değildir."""
    current = node.parent
    while current is not None and current.tag not in ("body", "html"):
        if current.tag in ("nav", "header", "footer") or _attr(current, "role") in (
            "navigation",
            "toolbar",
            "tablist",
            "menu",
        ):
            return True
        current = current.parent
    return False


def _button_choice_groups(tree: LexborHTMLParser) -> list[FieldGroup]:
    """Input yerine düğmelerle yapılmış seçenekli sorular (React/Vue "chip" deseni):
    aynı kapsayıcıda en az 2 seçenek düğmesi. Soru metni kapsayıcının üst öğesinden alınır
    (label + seçenekler)."""
    by_parent: dict[int, tuple[LexborNode, list[LexborNode]]] = {}
    for node in tree.css(CHOICE_SELECTOR):
        parent = node.parent
        if parent is None or not _is_choice_button(node) or _in_chrome(node):
            continue
        by_parent.setdefault(parent.mem_id, (parent, []))[1].append(node)
    groups = []
    for parent, buttons in by_parent.values():
        if len(buttons) < MIN_CHOICES or len(buttons) > MAX_CHOICE_BUTTONS:
            continue
        question = parent.parent or parent
        texts = [_attr(b, "aria-label") for b in buttons]
        groups.append(_group(question, texts, True, levels=0))
    return groups


def _group(
    node: LexborNode, texts: list[str], choice: bool, levels: int = QUESTION_LEVELS
) -> FieldGroup:
    question = squash(_ancestor(node, levels).text(deep=True, separator=" "))
    text = fold(squash(" ".join([*texts, question[:MAX_QUESTION_CHARS]])))
    categories = {}
    for name in keywords.sections(SPECIAL_PATH):
        hits = keywords.found(f"{SPECIAL_PATH}.{name}", text)
        if hits:
            categories[name] = hits
    return FieldGroup(node=node, text=text, choice=choice, categories=categories)


def qualified_categories(groups: list[FieldGroup]) -> list[str]:
    """Kategori sayılır: en az 2 farklı sinyal VEYA 1 sinyal + o kategoride seçenek listesi."""
    hits: dict[str, set[str]] = {}
    with_choice: set[str] = set()
    for group in groups:
        for name, words in group.categories.items():
            hits.setdefault(name, set()).update(words)
            if group.choice:
                with_choice.add(name)
    return [n for n, w in hits.items() if len(w) >= MIN_CHOICES or n in with_choice]


def is_data_form_page(groups: list[FieldGroup]) -> bool:
    """Bilgi toplayan bir form var mı (tek arama/bülten kutusundan fazlası)?"""
    return any(g.choice for g in groups) or len(groups) >= MIN_CHOICES
