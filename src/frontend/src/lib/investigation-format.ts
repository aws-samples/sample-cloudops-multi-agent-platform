export function investigationStatusLabel(workflowState: string): string {
  if (workflowState === "COMPLETED") return "Completed";
  if (workflowState === "FAILED") return "Failed";
  if (workflowState === "WAITING_CUSTOMER_APPROVAL") return "Waiting for approval";
  return "Running";
}

function elapsedSeconds(value?: string | number): number | null {
  if (!value) return null;
  const time = typeof value === "number" ? value : Date.parse(value);
  if (Number.isNaN(time)) return null;
  return Math.max(0, Math.floor((Date.now() - time) / 1000));
}

export function elapsedDuration(value?: string | number): string {
  const seconds = elapsedSeconds(value);
  if (seconds === null) return "";
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ${seconds % 60}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

export function relativeTime(value?: string | number): string {
  const seconds = elapsedSeconds(value);
  if (seconds === null) return "";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

export function absoluteTime(value?: string | null): string {
  if (!value) return "";
  const parsed = Date.parse(value);
  if (Number.isNaN(parsed)) return value;
  return new Date(parsed).toLocaleString([], {
    dateStyle: "medium",
    timeStyle: "short",
  });
}
