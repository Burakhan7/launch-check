"""Huni ölçümlerinin yorumlanması: form alanı türleri, kayıt formu seçimi, fiyat bilgisi, link
mesafesi ve ücretli plan sinyali. Tarayıcıdan bağımsızdır (birim testleri doğrudan çağırır).

Kelime listeleri `keywords/{tr,en}.yaml` → `funnel` (dile özgü). Tek kelime asla tek başına karar
vermez: kayıt formu için alan yapısı (şifre sayısı, e-posta) + düğme etiketi/adres birlikte aranır.
"""

import re
from collections import deque
from typing import Any
from urllib.parse import urlsplit

from launchcheck.checks.shared import keywords
from launchcheck.checks.shared.text import fold, tokens
from launchcheck.checks.web.conversion import auth_target
from launchcheck.deep.funnel_data import FormField, PricingInfo, SignupForm
from launchcheck.deep.render_data import RenderData

PAYMENT_IFRAMES = re.compile(
    r"stripe\.com|iyzipay|iyzico|paytr|braintree|adyen|checkout\.com|paypal"
)
NAME_TOKENS = {
    "ad",
    "adi",
    "adin",
    "adiniz",
    "adsoyad",
    "isim",
    "isminiz",
    "soyad",
    "soyadi",
    "soyadiniz",
    "name",
    "fullname",
    "firstname",
    "lastname",
    "surname",
    "fname",
    "lname",
}
TOKEN_KINDS: tuple[tuple[str, frozenset[str]], ...] = (
    ("card", frozenset({"card", "kart", "cc", "cvc", "cvv", "ccv", "iban", "cardnumber"})),
    ("email", frozenset({"email", "eposta", "mail", "emailadresi"})),
    ("tel", frozenset({"telefon", "phone", "gsm", "tel", "cep", "mobile", "mobil", "telno"})),
    ("date", frozenset({"dogum", "birth", "birthday", "dob", "birthdate"})),
)
AUTOCOMPLETE_KINDS = {
    "email": "email",
    "username": "email",
    "tel": "tel",
    "name": "name",
    "given-name": "name",
    "family-name": "name",
    "new-password": "password",
    "current-password": "password",
    "bday": "date",
}
MIN_SIGNUP_FIELDS = 2


def has_phrase(path: str, folded: str) -> list[str]:
    """Listedeki ifadelerden metinde geçenler. "/" veya "+" ile başlayanlar ("/ay", "+kdv")
    kelime sınırı olmadan aranır (rakamdan hemen sonra gelirler: "99/ay", "100+kdv")."""
    out = []
    for item in keywords.words(path):
        word = fold(item)
        hit = word in folded if not word[:1].isalnum() else keywords.has_word(item, folded)
        if hit and word.rstrip("*") not in out:
            out.append(word.rstrip("*"))
    return out


def classify_field(info: dict[str, Any]) -> str:
    kind = str(info.get("type") or "text")
    if kind == "password":
        return "password"
    if kind in ("email", "tel", "date"):
        return kind
    if kind in ("checkbox", "radio"):
        return "checkbox"
    auto = str(info.get("autocomplete") or "").split()
    auto_kind = next((AUTOCOMPLETE_KINDS[a] for a in auto if a in AUTOCOMPLETE_KINDS), None)
    if any(a.startswith("cc-") for a in auto):
        return "card"
    if auto_kind:
        return auto_kind
    words = set(tokens(f"{info.get('name', '')} {info.get('label', '')}"))
    for name, vocab in TOKEN_KINDS:
        if words & vocab:
            return name
    if words & NAME_TOKENS:
        return "name"
    return "select" if kind == "select" else "text" if kind in ("text", "textarea") else "other"


def _field(info: dict[str, Any]) -> FormField:
    return FormField(
        kind=classify_field(info),
        type=str(info.get("type") or ""),
        inputmode=str(info.get("inputmode") or ""),
        autocomplete=str(info.get("autocomplete") or ""),
        required=bool(info.get("required")),
        label=str(info.get("label") or "")[:60],
    )


def _is_signup(fields: list[FormField], buttons: str, register_url: bool) -> bool:
    passwords = sum(1 for f in fields if f.kind == "password")
    others = [f for f in fields if f.kind not in ("password", "checkbox")]
    has_email = any(f.kind in ("email", "tel") for f in fields)
    signup_button = bool(has_phrase("funnel.signup_buttons", buttons))
    login_button = bool(has_phrase("funnel.login_buttons", buttons))
    if passwords >= MIN_SIGNUP_FIELDS:
        return True
    if passwords == 1:
        # Giriş formu: şifre + tek kimlik alanı + "giriş" düğmesi
        return len(others) >= MIN_SIGNUP_FIELDS or (signup_button and not login_button)
    # Şifresiz kayıt (e-posta ile bağlantı): bülten formları elenir
    if has_email and (signup_button or register_url):
        return not has_phrase("funnel.newsletter", buttons) or register_url
    return False


def pick_signup(page_url: str, inspect: dict[str, Any]) -> SignupForm | None:
    """Sayfadaki en kapsamlı kayıt formu. Giriş, arama ve bülten formları kayıt sayılmaz."""
    register_url = auth_target(page_url) == "register"
    best: SignupForm | None = None
    for group in inspect.get("groups") or []:
        fields = [_field(f) for f in group.get("fields") or [] if isinstance(f, dict)]
        buttons = fold(" ".join(str(b) for b in group.get("buttons") or []))
        if not _is_signup(fields, buttons, register_url):
            continue
        consent = any(
            f.kind == "checkbox" and has_phrase("funnel.consent", fold(f.label)) for f in fields
        )
        iframes = " ".join(str(i) for i in group.get("iframes") or [])
        form = SignupForm(
            page=urlsplit(page_url).path or "/",
            fields=fields,
            social=[str(s) for s in inspect.get("social") or []],
            card=any(f.kind == "card" for f in fields) or bool(PAYMENT_IFRAMES.search(iframes)),
            consent_checkbox=consent,
            in_dialog=bool(group.get("dialog")),
        )
        if best is None or len(form.data_fields) > len(best.data_fields):
            best = form
    return best


def pricing_info(page_url: str, inspect: dict[str, Any]) -> PricingInfo:
    contexts = " | ".join(str(c) for c in inspect.get("priceContexts") or [])
    text = str(inspect.get("text") or "")
    prices = int(inspect.get("prices") or 0)
    periods = [
        name
        for name, path in (("monthly", "funnel.monthly"), ("yearly", "funnel.yearly"))
        if has_phrase(path, contexts or text)
    ]
    return PricingInfo(
        page=urlsplit(page_url).path or "/",
        prices=prices,
        currencies=[str(c) for c in inspect.get("currencies") or []][:5],
        vat_excluded=has_phrase("funnel.vat_excluded", contexts) if prices else [],
        free_tier=bool(has_phrase("funnel.free_tier", contexts or text)),
        periods=periods,
        quote_based=prices == 0 and bool(has_phrase("funnel.quote", text)),
    )


def _norm(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.netloc}{parts.path.rstrip('/') or '/'}"


def link_distances(render: RenderData | None, home: str) -> dict[str, int]:
    """Ana sayfadan tık mesafesi (render'ın topladığı iç link grafiği üzerinde BFS). Render'ın
    açmadığı sayfaların linkleri bilinmez: mesafe en fazla bir adım öteye kadar doğrudur."""
    graph: dict[str, list[str]] = {}
    if render is not None:
        for page in render.pages:
            links = [_norm(u) for u in page.internal_links]
            for url in (page.url, page.final_url):
                if url:
                    graph.setdefault(_norm(url), []).extend(links)
    start = _norm(home)
    distances = {start: 0}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        for nxt in graph.get(current, []):
            if nxt not in distances:
                distances[nxt] = distances[current] + 1
                queue.append(nxt)
    return distances


def distance(distances: dict[str, int], url: str) -> int | None:
    return distances.get(_norm(url))


def paid_signals(render: RenderData | None, home_text: str, pricing_found: bool) -> list[str]:
    signals = []
    if pricing_found:
        signals.append("pricing_page")
    words = has_phrase("funnel.paid_words", home_text)
    if words:
        signals.append("words:" + ",".join(words[:3]))
    if render is not None and any(p.sd.prices for p in render.pages):
        signals.append("prices_on_site")
    return signals
