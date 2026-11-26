"use client";

import { lazy, Suspense, useEffect, useRef, useState } from "react";

// Tema seçici sadece alt bilgi ekrana yaklaşınca yüklenir: ilk yükleme JS'ine girmez.
// next/dynamic yerine React.lazy: next/dynamic'in loadable çalışma zamanı her sayfanın ilk
// yüküne ~5 KB ekliyordu (bkz. docs/decisions.md, P10d).
const ThemeSwitcher = lazy(() => import("./ThemeSwitcher"));

export function LazyThemeSwitcher() {
  const ref = useRef<HTMLDivElement>(null);
  const [show, setShow] = useState(false);

  useEffect(() => {
    const node = ref.current;
    if (!node || typeof IntersectionObserver === "undefined") {
      setShow(true);
      return;
    }
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setShow(true);
          observer.disconnect();
        }
      },
      { rootMargin: "200px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  // Yer tutucu yüksekliği seçicininkiyle aynı: yüklenince düzen kaymaz (CLS)
  return (
    <div ref={ref} className="min-h-11">
      {show && (
        <Suspense fallback={null}>
          <ThemeSwitcher />
        </Suspense>
      )}
    </div>
  );
}
