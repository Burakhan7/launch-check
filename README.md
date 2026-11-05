# Launch Check

Vibe coding ile yapılmış web siteleri ve Android uygulamaları için ücretsiz, yayın öncesi kontrol aracı.
Proje bağlamı ve kurallar: [AGENTS.md](AGENTS.md). Tasarım kararları: [docs/decisions.md](docs/decisions.md).

## Hızlı başlangıç (Docker)

```bash
cp .env.example .env        # isteğe bağlı; yoksa yerel varsayılanlar kullanılır
docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build
```

- Web: http://localhost:3000 — API: http://localhost:8000/api/health — Caddy: http://localhost:8080

## Docker olmadan geliştirme

Backend ([apps/api](apps/api)) — [uv](https://docs.astral.sh/uv/) gerekir, Python 3.12'yi kendisi indirir:

```bash
cd apps/api
uv sync
uv run pytest
uv run ruff check . && uv run ruff format --check .
uv run uvicorn launchcheck.main:app --reload      # Postgres + Redis gerekir
uv run arq launchcheck.worker.settings.WorkerSettings
```

Frontend ([apps/web](apps/web)) — Node 22:

```bash
cd apps/web
npm install
npm run dev        # http://localhost:3000 → /tr
npm run lint && npm run format:check
```

## APK test fixture'ları

`apps/api/tests/fixtures/apk/*.apk` dosyaları küçük, gerçek ve imzalı APK'lardır. Yeniden üretmek için:

```bash
docker build -t lc-apk-fixtures apps/api/tests/fixtures/apk
docker run --rm -v "$PWD/apps/api/tests/fixtures/apk:/out" lc-apk-fixtures
```

apktool/jadx gerektiren testler (`pytest -m tools`) worker imajında çalışır; CI'daki `apk-tools` job'una bak.
