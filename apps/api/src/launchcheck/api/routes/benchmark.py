"""Kıyas kategorileri (PHASE2 §5): kullanıcı Faz 2'yi başlatırken kategoriyi seçebilir.
Sadece kategori anahtarı ve görünen adı döner; referans site listesi dönmez."""

from fastapi import APIRouter, Query

from launchcheck.deep.benchmark.categories import category_labels
from launchcheck.i18n import normalize_lang

router = APIRouter(prefix="/benchmark", tags=["benchmark"])


@router.get("/categories")
async def categories(lang: str | None = Query(default=None)) -> dict[str, list[dict[str, str]]]:
    return {"categories": category_labels(normalize_lang(lang))}
