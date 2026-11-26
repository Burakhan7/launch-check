/** Sınıf adlarını birleştirir (boş/yanlış değerler atlanır). Rare UI kaynağındaki clsx + tailwind-merge
 * yerine: tema seçici her sayfada yüklendiği için tailwind-merge (~29 KB) ana sayfa JS'ini büyütüyordu.
 * Çakışma birleştirmesi yapmaz; taban sınıfı ezmek gereken yerde Tailwind `!` önceliği kullanılır. */
export type ClassValue = string | false | null | undefined | 0;

export function cn(...values: ClassValue[]): string {
  return values.filter(Boolean).join(" ");
}
