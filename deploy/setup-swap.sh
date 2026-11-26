#!/usr/bin/env sh
# Küçük sunucu (ör. 2 vCPU / 4 GB) için swap dosyası. Tekrar çalıştırılabilir (idempotent).
# Kullanım (sunucuda, root):  sh deploy/setup-swap.sh [boyut, varsayılan 4G]
#
# Neden: tek bir derin tarama (Chromium + Lighthouse) tepe noktada ~2,5-4 GB bellek kullanıyor;
# 4 GB RAM'de diğer servislerle birlikte aşılabilir. Swap, OOM ile süreç öldürülmesi yerine
# yavaşlamayı seçer. Swappiness düşük tutulur: swap sadece taşma için kullanılır.
set -eu

SIZE="${1:-4G}"
SWAPFILE=/swapfile

if [ "$(id -u)" -ne 0 ]; then
  echo "root olarak çalıştır (sudo sh $0)" >&2
  exit 1
fi

if swapon --show=NAME --noheadings | grep -qx "$SWAPFILE"; then
  echo "swap zaten açık: $SWAPFILE"
else
  if [ ! -f "$SWAPFILE" ]; then
    fallocate -l "$SIZE" "$SWAPFILE" || dd if=/dev/zero of="$SWAPFILE" bs=1M count="$(numfmt --from=iec "$SIZE" | awk '{print int($1/1048576)}')"
    chmod 600 "$SWAPFILE"
    mkswap "$SWAPFILE"
  fi
  swapon "$SWAPFILE"
  echo "swap açıldı: $SWAPFILE ($SIZE)"
fi

grep -q "^$SWAPFILE " /etc/fstab || echo "$SWAPFILE none swap sw 0 0" >> /etc/fstab

# Swap'e sadece gerçekten gerektiğinde git; dosya sistemi önbelleğini fazla atma
cat > /etc/sysctl.d/99-launchcheck-swap.conf <<'CONF'
vm.swappiness=10
vm.vfs_cache_pressure=50
CONF
sysctl -p /etc/sysctl.d/99-launchcheck-swap.conf >/dev/null

free -h
