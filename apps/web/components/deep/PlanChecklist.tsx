"use client";

import { useState } from "react";
import type { Finding } from "@/lib/api";
import { readDone, writeDone } from "@/lib/planStorage";
import { browserStorage } from "@/lib/storage";
import { TaskList, type Task } from "../rare/task-list";

/** Öncelik planı grubunun yapılacaklar listesi (Rare UI Task list). İşaretler localStorage'da. */
export function PlanChecklist({
  scanId,
  findings,
  minutesLabel,
}: {
  scanId: string;
  findings: Finding[];
  minutesLabel: (minutes: number) => string;
}) {
  const [done, setDone] = useState<string[]>(() => readDone(browserStorage(), scanId));
  const tasks: Task[] = findings.map((f) => ({
    id: f.id ?? f.check_id,
    label: f.impact ? `${f.title} · ${minutesLabel(f.impact.fix_minutes)}` : f.title,
    done: done.includes(f.id ?? f.check_id),
  }));
  return (
    <TaskList
      size="sm"
      accent="var(--brand)"
      tasks={tasks}
      onTasksChange={(next) => {
        const ids = next.filter((task) => task.done).map((task) => task.id);
        // Diğer gruplardaki işaretler korunur: güncel hâl depodan okunur (her grup ayrı bileşen)
        const stored = readDone(browserStorage(), scanId);
        const others = (stored.length ? stored : done).filter(
          (id) => !tasks.some((task) => task.id === id),
        );
        const merged = [...others, ...ids];
        setDone(merged);
        writeDone(browserStorage(), scanId, merged);
      }}
      className="w-full!"
    />
  );
}
