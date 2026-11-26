export type CalendarPresetId =
  | "today"
  | "7d"
  | "30d"
  | "this_month"
  | "last_month"
  | "3m"
  | "6m"
  | "1y";

export const CALENDAR_PRESETS: Array<{ id: CalendarPresetId; label_th: string }> = [
  { id: "7d", label_th: "7 วันล่าสุด" },
  { id: "30d", label_th: "30 วันล่าสุด" },
  { id: "this_month", label_th: "เดือนนี้" },
  { id: "last_month", label_th: "เดือนที่แล้ว" },
  { id: "3m", label_th: "3 เดือนล่าสุด" },
  { id: "6m", label_th: "6 เดือนล่าสุด" },
  { id: "1y", label_th: "1 ปีล่าสุด" },
];

export function toLocalIsoDate(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

export function getLocalTzOffsetMinutes(): number {
  return -new Date().getTimezoneOffset();
}

export function resolveCalendarPreset(
  presetId: CalendarPresetId,
  now: Date = new Date()
): { startDate: string; endDate: string } {
  const end = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  let start = new Date(end);

  if (presetId === "today") {
    return {
      startDate: toLocalIsoDate(end),
      endDate: toLocalIsoDate(end),
    };
  }
  if (presetId === "7d") {
    start.setDate(end.getDate() - 6);
  } else if (presetId === "30d") {
    start.setDate(end.getDate() - 29);
  } else if (presetId === "this_month") {
    start = new Date(end.getFullYear(), end.getMonth(), 1);
  } else if (presetId === "last_month") {
    const firstLast = new Date(end.getFullYear(), end.getMonth() - 1, 1);
    const lastDayLast = new Date(end.getFullYear(), end.getMonth(), 0);
    return {
      startDate: toLocalIsoDate(firstLast),
      endDate: toLocalIsoDate(lastDayLast),
    };
  } else if (presetId === "3m") {
    start.setMonth(end.getMonth() - 3);
  } else if (presetId === "6m") {
    start.setMonth(end.getMonth() - 6);
  } else if (presetId === "1y") {
    start.setFullYear(end.getFullYear() - 1);
  }

  return {
    startDate: toLocalIsoDate(start),
    endDate: toLocalIsoDate(end),
  };
}

export function formatThaiDateSpan(startSec?: number | null, endSec?: number | null): string {
  if (!startSec || !endSec) return "";
  const fmt = (sec: number) =>
    new Date(sec * 1000).toLocaleString("th-TH", {
      timeZone: "Asia/Bangkok",
      day: "2-digit",
      month: "short",
      year: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
  return `${fmt(startSec)} – ${fmt(endSec)}`;
}
