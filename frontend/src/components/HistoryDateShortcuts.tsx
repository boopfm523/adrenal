import { localDate, shiftIsoDate } from "../time";

export function HistoryDateShortcuts({ dateFrom, dateTo, timezone, label, onSelect }: {
  dateFrom: string;
  dateTo: string;
  timezone: string;
  label: string;
  onSelect: (day: string) => void;
}): React.JSX.Element {
  let today: string | null = null;
  try {
    today = localDate(new Date(), timezone);
  } catch {
    // Keep the editable timezone field usable while its value is incomplete.
  }
  const shortcuts = [
    { label: "Today", day: today },
    { label: "Yesterday", day: today === null ? null : shiftIsoDate(today, -1) },
    { label: "2 days ago", day: today === null ? null : shiftIsoDate(today, -2) },
  ];
  return <div className="healthcurve-date-shortcuts" role="group" aria-label={label}>
    <span>Quick dates:</span>
    {shortcuts.map((shortcut) => <button key={shortcut.label} type="button" disabled={shortcut.day === null} className={shortcut.day !== null && dateFrom === shortcut.day && dateTo === shortcut.day ? undefined : "button-secondary"} aria-pressed={shortcut.day !== null && dateFrom === shortcut.day && dateTo === shortcut.day} onClick={() => { if (shortcut.day !== null) onSelect(shortcut.day); }}>{shortcut.label}</button>)}
  </div>;
}
