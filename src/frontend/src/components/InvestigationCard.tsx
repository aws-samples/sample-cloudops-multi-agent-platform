"use client";

import { useEffect, useState } from "react";
import {
  AlertCircle,
  CheckCircle2,
  ChevronRight,
  CircleDashed,
  Clock3,
  ScanSearch,
} from "lucide-react";
import { useInvestigationActivity } from "@/lib/investigation-activity";
import {
  elapsedDuration,
  investigationStatusLabel,
} from "@/lib/investigation-format";
import {
  parseInvestigationMarker,
  type InvestigationReference,
} from "@/lib/investigation-result";

export function InvestigationCard({ markerText }: { markerText: string }) {
  const reference = parseInvestigationMarker(markerText);
  return reference ? <InvestigationCardBody reference={reference} /> : null;
}

function InvestigationCardBody({ reference }: { reference: InvestigationReference }) {
  const activity = useInvestigationActivity(reference.investigationId);
  const investigation = activity.data?.investigation;
  const workflowState = investigation?.workflowState || "RUNNING";
  const label = investigationStatusLabel(workflowState);
  const [, setClock] = useState(0);

  useEffect(() => {
    if (investigation?.terminal) return;
    const timer = setInterval(() => setClock((value) => value + 1), 1_000);
    return () => clearInterval(timer);
  }, [investigation?.terminal]);

  const open = () => {
    window.dispatchEvent(
      new CustomEvent("open-investigation", {
        detail: { investigationId: reference.investigationId },
      }),
    );
  };

  const StatusIcon = workflowState === "COMPLETED"
    ? CheckCircle2
    : workflowState === "FAILED"
      ? AlertCircle
      : workflowState === "WAITING_CUSTOMER_APPROVAL"
        ? Clock3
        : CircleDashed;

  return (
    <button
      type="button"
      onClick={open}
      className="w-full my-3 p-3.5 flex items-center gap-3 text-left rounded-lg transition-colors hover:bg-[var(--bg-elevated)]"
      style={{ background: "var(--bg-surface)", border: "1px solid var(--border-default)" }}
      aria-label={`View DevOps Agent investigation: ${label}`}
    >
      <div
        className="h-9 w-9 shrink-0 rounded-md flex items-center justify-center"
        style={{ background: "var(--accent-surface)", color: "var(--accent-ai)" }}
      >
        <ScanSearch className="h-4.5 w-4.5" aria-hidden="true" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-sm font-medium truncate" style={{ color: "var(--text-primary)" }}>
            DevOps Agent investigation
          </span>
        </div>
        <div className="text-xs truncate mt-0.5" style={{ color: "var(--text-secondary)" }}>
          {investigation?.title || reference.title}
        </div>
        <div className="flex items-center gap-1.5 text-xs mt-1" style={{ color: "var(--text-muted)" }}>
          <StatusIcon
            className={workflowState === "RUNNING" ? "h-3 w-3 animate-spin" : "h-3 w-3"}
            style={{ color: workflowState === "FAILED" ? "var(--danger)" : "var(--accent)" }}
            aria-hidden="true"
          />
          <span>{label}</span>
          {!investigation?.terminal && investigation?.createdAt && (
            <span>· {elapsedDuration(investigation.createdAt)}</span>
          )}
          {activity.error && <span>· Updates delayed</span>}
        </div>
      </div>
      <div className="flex items-center gap-1 shrink-0 text-xs" style={{ color: "var(--accent-ai)" }}>
        <span>View</span>
        <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
      </div>
    </button>
  );
}
