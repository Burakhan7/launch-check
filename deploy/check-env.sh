#!/usr/bin/env sh
# Prod'a çıkmadan önce .env kontrolü: yer tutucu veya tutarsız değer kaldıysa durdurur.
# Kullanım (repo kökünde): sh deploy/check-env.sh [.env yolu]
set -eu

ENV_FILE="${1:-.env}"
errors=0
warns=0

if [ ! -f "$ENV_FILE" ]; then
  echo "HATA: $ENV_FILE yok. cp .env.example .env ve PROD: işaretli değerleri doldur." >&2
  exit 1
fi

# Son tanımı alır; satır sonu yorumunu ( #...) ve çevreleyen tırnakları atar. `source` kullanılmaz:
# değerlerde parantez/boşluk var (ör. bot User-Agent).
val() {
  grep -E "^$1=" "$ENV_FILE" | tail -n 1 | cut -d= -f2- \
    | sed -e 's/[[:space:]]#.*$//' -e 's/[[:space:]]*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}

fail() { echo "HATA: $1"; errors=$((errors + 1)); }
warn() { echo "UYARI: $1"; warns=$((warns + 1)); }

LC_ENV=$(val LC_ENV)
DOMAIN=$(val DOMAIN)
BASE_URL=$(val LC_PUBLIC_BASE_URL)
PG_PASS=$(val POSTGRES_PASSWORD)
PG_USER=$(val POSTGRES_USER)
DB_URL=$(val LC_DATABASE_URL)
SALT=$(val LC_RATE_LIMIT_IP_SALT)
BOT_UA=$(val LC_BOT_USER_AGENT)
CONTACT=$(val NEXT_PUBLIC_CONTACT_URL)

[ "$LC_ENV" = "prod" ] || fail "LC_ENV=prod olmalı (şu an: '${LC_ENV}')"

case "$DOMAIN" in
  "" | localhost | *://* | */*) fail "DOMAIN çıplak alan adı olmalı (ör. launchcheck.com.tr), şu an: '${DOMAIN}'" ;;
esac

[ "$BASE_URL" = "https://$DOMAIN" ] || fail "LC_PUBLIC_BASE_URL https://$DOMAIN olmalı (şu an: '${BASE_URL}')"

if [ "$PG_PASS" = "launchcheck" ] || [ "${#PG_PASS}" -lt 16 ]; then
  fail "POSTGRES_PASSWORD varsayılan veya kısa. Üret: openssl rand -hex 24"
fi
case "$DB_URL" in
  *"://${PG_USER:-launchcheck}:${PG_PASS}@"*) ;;
  *) fail "LC_DATABASE_URL içindeki kullanıcı/şifre POSTGRES_USER/POSTGRES_PASSWORD ile aynı değil" ;;
esac

case "$SALT" in
  change-me* | "") fail "LC_RATE_LIMIT_IP_SALT yer tutucu. Üret: openssl rand -hex 32" ;;
  *) [ "${#SALT}" -ge 24 ] || fail "LC_RATE_LIMIT_IP_SALT en az 24 karakter olmalı" ;;
esac

case "$BOT_UA" in
  *launchcheck.example*) fail "LC_BOT_USER_AGENT hâlâ örnek adresi gösteriyor; https://$DOMAIN/bot yaz" ;;
  *"$DOMAIN/bot"*) ;;
  *) warn "LC_BOT_USER_AGENT https://$DOMAIN/bot adresini içermiyor (botun tanıtım sayfası)" ;;
esac

case "$CONTACT" in
  "") fail "NEXT_PUBLIC_CONTACT_URL boş (rapordaki iletişim linki); https://... veya mailto:..." ;;
  *launchcheck.example*) fail "NEXT_PUBLIC_CONTACT_URL hâlâ örnek adres" ;;
  https://* | mailto:*) ;;
  *) fail "NEXT_PUBLIC_CONTACT_URL https:// veya mailto: ile başlamalı" ;;
esac

# Küçük sunucuda swap yoksa derin tarama bellek yetmezliğinden ölebilir (bkz. setup-swap.sh)
if command -v free >/dev/null 2>&1; then
  mem_mb=$(free -m | awk '/^Mem:/ {print $2}')
  swap_mb=$(free -m | awk '/^Swap:/ {print $2}')
  if [ "${mem_mb:-0}" -lt 7000 ] && [ "${swap_mb:-0}" -lt 2000 ]; then
    warn "RAM ${mem_mb} MB ve swap ${swap_mb} MB: önce sudo sh deploy/setup-swap.sh çalıştır"
  fi
fi

if [ "$errors" -gt 0 ]; then
  echo "$errors hata, $warns uyarı: düzeltmeden dağıtma." >&2
  exit 1
fi
echo "Tamam ($warns uyarı)."
