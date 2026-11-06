import type { ScoreLabel } from "@/lib/api";

export const SCORE_COLOR: Record<ScoreLabel, string> = {
  not_ready: "var(--score-bad)",
  needs_work: "var(--score-mid)",
  good: "var(--score-good)",
};

const RADIUS = 52;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

export function ScoreRing({ score, label }: { score: number; label: ScoreLabel }) {
  const offset = CIRCUMFERENCE * (1 - Math.max(0, Math.min(100, score)) / 100);
  return (
    <div className="relative h-36 w-36 shrink-0">
      <svg viewBox="0 0 120 120" className="h-full w-full -rotate-90" aria-hidden="true">
        <circle
          cx="60"
          cy="60"
          r={RADIUS}
          fill="none"
          stroke="var(--surface-strong)"
          strokeWidth="10"
        />
        <circle
          cx="60"
          cy="60"
          r={RADIUS}
          fill="none"
          stroke={SCORE_COLOR[label]}
          strokeWidth="10"
          strokeLinecap="round"
          strokeDasharray={CIRCUMFERENCE}
          strokeDashoffset={offset}
          className="transition-[stroke-dashoffset] duration-700"
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="text-4xl font-bold tabular-nums">{score}</span>
        <span className="text-xs text-muted">/ 100</span>
      </div>
    </div>
  );
}

export function CategoryBar({ name, score }: { name: string; score: number }) {
  const color =
    score <= 49 ? SCORE_COLOR.not_ready : score <= 79 ? SCORE_COLOR.needs_work : SCORE_COLOR.good;
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex justify-between text-sm">
        <span>{name}</span>
        <span className="font-semibold tabular-nums">{score}</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-surface-strong">
        <div className="h-full rounded-full" style={{ width: `${score}%`, background: color }} />
      </div>
    </div>
  );
}
