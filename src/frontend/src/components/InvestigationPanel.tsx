"use client";

import { useEffect, useState } from "react";
import {
  Activity,
  AlertCircle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  CircleDashed,
  Clock3,
  ExternalLink,
  RefreshCw,
  X,
} from "lucide-react";
import { useFocusTrap } from "@/lib/a11y/use-focus-trap";
import { useInvestigationActivity } from "@/lib/investigation-activity";
import {
  absoluteTime,
  investigationStatusLabel,
  relativeTime,
} from "@/lib/investigation-format";
import type { InvestigationMilestone } from "@/lib/runtime-client";

function TimelineEntry({
  milestone,
  last,
}: {
  milestone: InvestigationMilestone;
  last: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="relative flex gap-3 pb-5 last:pb-0">
      {!last && (
        <div
          className="absolute left-[5px] top-3 bottom-0 w-px"
          style={{ background: "var(--border-default)" }}
        />
      )}
      <div
        className="mt-1 h-3 w-3 rounded-full shrink-0 z-[1]"
        style={{ background: "var(--accent)", border: "2px solid var(--bg-secondary)" }}
      />
      <div className="min-w-0 flex-1">
        <div className="text-sm leading-snug" style={{ color: "var(--text-primary)" }}>
          {milestone.title}
        </div>
        <div className="flex items-center gap-2 mt-1 text-xs" style={{ color: "var(--text-muted)" }}>
          <span>{relativeTime(milestone.createdAt)}</span>
          {milestone.details && (
            <button
              type="button"
              onClick={() => setExpanded((value) => !value)}
              className="inline-flex items-center gap-0.5 hover:text-[var(--text-secondary)]"
              aria-expanded={expanded}
            >
              {expanded ? "Collapse" : "Expand"}
              {expanded
                ? <ChevronDown className="h-3 w-3" aria-hidden="true" />
                : <ChevronRight className="h-3 w-3" aria-hidden="true" />}
            </button>
          )}
        </div>
        {expanded && (
          <p
            className="mt-2 whitespace-pre-wrap break-words rounded-md p-2.5 text-xs leading-relaxed"
            style={{ background: "var(--bg-surface)", color: "var(--text-secondary)", border: "1px solid var(--border-subtle)" }}
          >
            {milestone.details}
          </p>
        )}
      </div>
    </div>
  );
}

export function InvestigationPanel({
  investigationId,
  onClose,
}: {
  investigationId: string;
  onClose: () => void;
}) {
  const activity = useInvestigationActivity(
    investigationId,
    { followMitigation: true },
  );
  const investigation = activity.data?.investigation;
  const outcome = activity.data?.outcome;
  const milestones = activity.data?.activity || [];
  const workflowState = investigation?.workflowState || "RUNNING";
  const label = investigationStatusLabel(workflowState);
  const panelRef = useFocusTrap<HTMLElement>(true, onClose);
  const [, setClock] = useState(0);

  useEffect(() => {
    const timer = setInterval(() => setClock((value) => value + 1), 1_000);
    return () => clearInterval(timer);
  }, []);

  const StatusIcon = workflowState === "COMPLETED"
    ? CheckCircle2
    : workflowState === "FAILED"
      ? AlertCircle
      : workflowState === "WAITING_CUSTOMER_APPROVAL"
        ? Clock3
        : CircleDashed;
  const mitigation = outcome?.mitigation;

  return (
    <aside
      ref={panelRef}
      className="investigation-panel flex flex-col h-dvh shrink-0"
      style={{ background: "var(--bg-secondary)", borderLeft: "1px solid var(--border-subtle)" }}
      aria-label="DevOps Agent investigation activity"
    >
      <header
        className="h-14 px-4 flex items-center gap-3 shrink-0"
        style={{ borderBottom: "1px solid var(--border-subtle)" }}
      >
        <Activity className="h-4 w-4 shrink-0" style={{ color: "var(--accent-ai)" }} aria-hidden="true" />
        <div className="min-w-0 flex-1">
          <div className="text-xs font-medium" style={{ color: "var(--text-primary)" }}>
            DevOps Agent
          </div>
          <div className="flex items-center gap-1.5 text-xs" style={{ color: "var(--text-muted)" }}>
            <StatusIcon
              className={workflowState === "RUNNING" ? "h-3 w-3 animate-spin" : "h-3 w-3"}
              style={{ color: workflowState === "FAILED" ? "var(--danger)" : "var(--accent)" }}
              aria-hidden="true"
            />
            <span>{label}</span>
          </div>
        </div>
        <button
          type="button"
          onClick={activity.refresh}
          disabled={activity.status === "loading" || activity.refreshing}
          className="h-8 w-8 flex items-center justify-center rounded-md hover:bg-[var(--bg-elevated)] disabled:opacity-50"
          style={{ color: "var(--text-muted)" }}
          aria-label="Reload saved investigation status"
          title="Reload saved status"
        >
          <RefreshCw className={activity.status === "loading" || activity.refreshing ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
        </button>
        <button
          type="button"
          onClick={onClose}
          className="h-8 w-8 flex items-center justify-center rounded-md hover:bg-[var(--bg-elevated)]"
          style={{ color: "var(--text-muted)" }}
          aria-label="Close investigation panel"
          title="Close"
        >
          <X className="h-4 w-4" />
        </button>
      </header>

      <div className="flex-1 min-h-0 overflow-y-auto px-5 py-5">
        {activity.status === "loading" && !investigation ? (
          <div className="space-y-3" aria-label="Loading investigation">
            <div className="skeleton h-5 rounded w-3/4" />
            <div className="skeleton h-4 rounded w-1/2" />
            <div className="skeleton h-24 rounded mt-6" />
          </div>
        ) : activity.status === "error" && !investigation ? (
          <div className="h-full flex flex-col items-center justify-center gap-3 text-center">
            <AlertCircle className="h-5 w-5" style={{ color: "var(--danger)" }} aria-hidden="true" />
            <p className="text-sm" style={{ color: "var(--text-secondary)" }}>
              Investigation activity is temporarily unavailable.
            </p>
            <button
              type="button"
              onClick={activity.refresh}
              className="px-3 py-1.5 rounded-md text-sm"
              style={{ color: "var(--text-primary)", border: "1px solid var(--border-default)" }}
            >
              Retry
            </button>
          </div>
        ) : investigation ? (
          <>
            <section className="pb-5" style={{ borderBottom: "1px solid var(--border-subtle)" }}>
              <h2 className="text-base font-semibold leading-snug" style={{ color: "var(--text-primary)" }}>
                {investigation.title}
              </h2>
              <p className="text-xs mt-1.5" style={{ color: "var(--text-muted)" }}>
                Started {relativeTime(investigation.createdAt)}
              </p>
              {investigation.providerUrl && (
                <a
                  href={investigation.providerUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1.5 mt-3 text-sm hover:underline"
                  style={{ color: "var(--accent)" }}
                >
                  Open in DevOps Agent
                  <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                </a>
              )}
              {workflowState === "FAILED" && investigation.reason && (
                <p className="text-sm mt-3" style={{ color: "var(--danger)" }}>
                  {investigation.reason.replaceAll("_", " ").toLowerCase()}
                </p>
              )}
            </section>

            {workflowState === "COMPLETED" && (
              <section className="py-5" style={{ borderBottom: "1px solid var(--border-subtle)" }}>
                <h3 className="text-xs font-semibold uppercase mb-3" style={{ color: "var(--text-muted)", letterSpacing: 0 }}>
                  Outcome
                </h3>

                <div className="py-3" style={{ borderBottom: "1px solid var(--border-subtle)" }}>
                  <div className="text-xs font-semibold mb-1.5" style={{ color: "var(--text-muted)" }}>
                    Incident
                  </div>
                  <div className="text-sm font-medium leading-snug" style={{ color: "var(--text-primary)" }}>
                    {outcome?.incident?.title || "Incident details unavailable"}
                  </div>
                  {outcome?.incident?.description && (
                    <p className="mt-1.5 text-sm leading-relaxed" style={{ color: "var(--text-secondary)" }}>
                      {outcome.incident.description}
                    </p>
                  )}
                  {outcome?.incident?.startedAt && (
                    <p className="mt-1.5 text-xs" style={{ color: "var(--text-muted)" }}>
                      {absoluteTime(outcome.incident.startedAt)}
                    </p>
                  )}
                </div>

                <div className="py-3" style={{ borderBottom: "1px solid var(--border-subtle)" }}>
                  <div className="text-xs font-semibold mb-1.5" style={{ color: "var(--text-muted)" }}>
                    Root Cause
                  </div>
                  <div className="text-sm font-medium leading-snug" style={{ color: "var(--text-primary)" }}>
                    {outcome?.rootCause?.title || "Root cause unavailable"}
                  </div>
                  {outcome?.rootCause?.description && (
                    <p className="mt-1.5 text-sm leading-relaxed" style={{ color: "var(--text-secondary)" }}>
                      {outcome.rootCause.description}
                    </p>
                  )}
                </div>

                <div className="pt-3">
                  <div className="text-xs font-semibold mb-1.5" style={{ color: "var(--text-muted)" }}>
                    Mitigation
                  </div>
                  <div className="text-sm font-medium leading-snug" style={{ color: "var(--text-primary)" }}>
                    {mitigation?.action || (
                      mitigation?.status === "IN_PROGRESS"
                        ? "Mitigation in progress"
                        : mitigation?.terminal
                          ? `Mitigation ${mitigation.status.toLowerCase().replaceAll("_", " ")}`
                          : mitigation
                            ? "Waiting for mitigation outcome"
                            : "No mitigation reported by DevOps Agent"
                    )}
                  </div>
                  {mitigation?.description && (
                    <p className="mt-1.5 text-sm leading-relaxed" style={{ color: "var(--text-secondary)" }}>
                      {mitigation.description}
                    </p>
                  )}
                  {mitigation?.updatedAt && (
                    <p className="mt-1.5 text-xs" style={{ color: "var(--text-muted)" }}>
                      {absoluteTime(mitigation.updatedAt)}
                    </p>
                  )}
                </div>
              </section>
            )}

            <section className="pt-5">
              <h3 className="text-xs font-semibold uppercase mb-4" style={{ color: "var(--text-muted)", letterSpacing: 0 }}>
                Activity
              </h3>
              <TimelineEntry
                milestone={{
                  id: "investigation-started",
                  title: "Investigation started",
                  createdAt: investigation.createdAt,
                  details: "",
                  kind: "finding",
                }}
                last={milestones.length === 0 && !investigation.terminal}
              />
              {milestones.map((milestone, index) => (
                <TimelineEntry
                  key={milestone.id}
                  milestone={milestone}
                  last={index === milestones.length - 1 && !investigation.terminal}
                />
              ))}
              {investigation.terminal ? (
                <TimelineEntry
                  milestone={{
                    id: "investigation-finished",
                    title: workflowState === "COMPLETED"
                      ? "Investigation completed"
                      : "Investigation failed",
                    createdAt: investigation.completedAt || investigation.updatedAt,
                    details: workflowState === "FAILED" && investigation.reason
                      ? investigation.reason.replaceAll("_", " ").toLowerCase()
                      : "",
                    kind: workflowState === "FAILED" ? "gap" : "finding",
                  }}
                  last
                />
              ) : milestones.length === 0 ? (
                <div className="flex gap-3">
                  <CircleDashed className="h-3.5 w-3.5 mt-0.5 animate-spin" style={{ color: "var(--accent)" }} aria-hidden="true" />
                  <span className="text-sm" style={{ color: "var(--text-secondary)" }}>
                    Reviewing telemetry…
                  </span>
                </div>
              ) : null}
            </section>
          </>
        ) : null}
      </div>

      {investigation && (
        <footer
          className="px-5 py-3 shrink-0 text-xs"
          style={{ color: "var(--text-muted)", borderTop: "1px solid var(--border-subtle)" }}
        >
          Last updated {relativeTime(activity.lastCheckedAt || investigation.updatedAt)}
          {activity.error && " · Updates delayed"}
        </footer>
      )}
    </aside>
  );
}
