import type {
  InvestigationActivityResponse,
  InvestigationFetchResult,
} from "./runtime-client";

export interface InvestigationActivityState {
  status: "idle" | "loading" | "ready" | "error";
  refreshing?: boolean;
  data?: InvestigationActivityResponse;
  error?: string;
  lastCheckedAt?: number;
}

export interface InvestigationActivitySubscription {
  subscribe(
    investigationId: string,
    listener: () => void,
    options?: InvestigationSubscriptionOptions,
  ): () => void;
  getSnapshot(investigationId: string): InvestigationActivityState;
  refresh(investigationId: string): void;
}

export interface InvestigationSubscriptionOptions {
  followMitigation?: boolean;
}

export type FetchInvestigation = (
  investigationId: string,
  etag?: string,
  refresh?: boolean,
) => Promise<InvestigationFetchResult>;

export interface VisibilitySource {
  hidden: boolean;
  addEventListener(type: "visibilitychange", listener: () => void): void;
}

type Entry = {
  state: InvestigationActivityState;
  etag?: string;
  listeners: Map<() => void, InvestigationSubscriptionOptions>;
  timer: ReturnType<typeof setTimeout> | null;
  failures: number;
  polling: boolean;
  generation: number;
  missingMitigationPolls: number;
  refreshPending: boolean;
};

const EMPTY_STATE: InvestigationActivityState = { status: "idle" };

export class PollingInvestigationActivity implements InvestigationActivitySubscription {
  private entries = new Map<string, Entry>();
  private fetchInvestigation: FetchInvestigation;
  private pollIntervalMs: number;
  private completedPollIntervalMs: number;
  private retryDelaysMs: number[];
  private maxMissingMitigationPolls: number;
  private visibility?: VisibilitySource;

  constructor(
    fetchInvestigation: FetchInvestigation,
    options: {
      pollIntervalMs?: number;
      completedPollIntervalMs?: number;
      retryDelaysMs?: number[];
      maxMissingMitigationPolls?: number;
      visibility?: VisibilitySource;
    } = {},
  ) {
    this.fetchInvestigation = fetchInvestigation;
    this.pollIntervalMs = options.pollIntervalMs ?? 5_000;
    this.completedPollIntervalMs = options.completedPollIntervalMs ?? 30_000;
    this.retryDelaysMs = options.retryDelaysMs || [5_000, 10_000, 20_000, 30_000];
    this.maxMissingMitigationPolls = options.maxMissingMitigationPolls ?? 6;
    this.visibility = options.visibility || (
      typeof document !== "undefined" ? document : undefined
    );
    this.visibility?.addEventListener("visibilitychange", this.handleVisibilityChange);
  }

  subscribe(
    investigationId: string,
    listener: () => void,
    options: InvestigationSubscriptionOptions = {},
  ): () => void {
    const entry = this.entry(investigationId);
    entry.listeners.set(listener, options);
    if (this.shouldPoll(entry) && !entry.polling && !entry.timer) {
      void this.poll(investigationId);
    }
    return () => {
      entry.listeners.delete(listener);
      if (entry.listeners.size === 0) {
        if (entry.timer) clearTimeout(entry.timer);
        entry.timer = null;
        entry.generation += 1;
      } else if (!this.shouldPoll(entry) && entry.timer) {
        clearTimeout(entry.timer);
        entry.timer = null;
      }
    };
  }

  getSnapshot(investigationId: string): InvestigationActivityState {
    return this.entries.get(investigationId)?.state || EMPTY_STATE;
  }

  refresh(investigationId: string): void {
    const entry = this.entry(investigationId);
    if (entry.timer) clearTimeout(entry.timer);
    entry.timer = null;
    void this.poll(investigationId, true);
  }

  private entry(investigationId: string): Entry {
    let entry = this.entries.get(investigationId);
    if (!entry) {
      entry = {
        state: EMPTY_STATE,
        listeners: new Map(),
        timer: null,
        failures: 0,
        polling: false,
        generation: 0,
        missingMitigationPolls: 0,
        refreshPending: false,
      };
      this.entries.set(investigationId, entry);
    }
    return entry;
  }

  private emit(entry: Entry, state: InvestigationActivityState): void {
    entry.state = state;
    for (const listener of entry.listeners.keys()) listener();
  }

  private shouldPoll(entry: Entry): boolean {
    if (!entry.state.data) return true;
    if (!entry.state.data.investigation.terminal) return true;
    const followsMitigation = [...entry.listeners.values()].some(
      (options) => options.followMitigation,
    );
    if (!followsMitigation) return false;
    const mitigation = entry.state.data.outcome.mitigation;
    return mitigation
      ? !mitigation.terminal
      : entry.missingMitigationPolls < this.maxMissingMitigationPolls;
  }

  private nextDelay(entry: Entry): number {
    return entry.state.data?.investigation.terminal
      ? this.completedPollIntervalMs
      : this.pollIntervalMs;
  }

  private schedule(investigationId: string, delay: number): void {
    const entry = this.entry(investigationId);
    if (entry.listeners.size === 0 || !this.shouldPoll(entry)) return;
    if (entry.timer) clearTimeout(entry.timer);
    entry.timer = setTimeout(() => {
      entry.timer = null;
      void this.poll(investigationId);
    }, delay);
  }

  private async poll(investigationId: string, force = false): Promise<void> {
    const entry = this.entry(investigationId);
    if (entry.polling) {
      if (force) entry.refreshPending = true;
      return;
    }
    if (entry.listeners.size === 0) return;
    if (!force && !this.shouldPoll(entry)) return;
    if (this.visibility?.hidden) return;

    entry.polling = true;
    const generation = entry.generation;
    if (!entry.state.data) {
      this.emit(entry, { status: "loading" });
    } else if (force) {
      this.emit(entry, { ...entry.state, refreshing: true, error: undefined });
    }
    try {
      const result = await this.fetchInvestigation(
        investigationId,
        force ? undefined : entry.etag,
        force,
      );
      if (generation !== entry.generation || entry.listeners.size === 0) return;
      entry.failures = 0;
      entry.etag = result.etag || entry.etag;
      if (result.notModified) {
        this.emit(entry, {
          ...entry.state,
          status: entry.state.data ? "ready" : entry.state.status,
          refreshing: false,
          error: undefined,
          lastCheckedAt: Date.now(),
        });
      } else if (result.data) {
        this.emit(entry, {
          status: "ready",
          refreshing: false,
          data: result.data,
          lastCheckedAt: Date.now(),
        });
      }
      if (
        entry.state.data?.investigation.terminal
        && !entry.state.data.outcome.mitigation
      ) {
        entry.missingMitigationPolls += 1;
      } else {
        entry.missingMitigationPolls = 0;
      }
      this.schedule(investigationId, this.nextDelay(entry));
    } catch (error) {
      if (generation !== entry.generation || entry.listeners.size === 0) return;
      const message = error instanceof Error ? error.message : "Investigation update failed";
      this.emit(entry, {
        ...entry.state,
        status: entry.state.data ? "ready" : "error",
        refreshing: false,
        error: message,
        lastCheckedAt: Date.now(),
      });
      const delay = this.retryDelaysMs[
        Math.min(entry.failures, this.retryDelaysMs.length - 1)
      ];
      entry.failures += 1;
      this.schedule(investigationId, delay);
    } finally {
      entry.polling = false;
      const refreshPending = entry.refreshPending;
      entry.refreshPending = false;
      if (refreshPending && entry.listeners.size > 0) {
        void this.poll(investigationId, true);
        return;
      }
      if (
        generation !== entry.generation
        && entry.listeners.size > 0
        && this.shouldPoll(entry)
      ) {
        void this.poll(investigationId);
      }
    }
  }

  private handleVisibilityChange = (): void => {
    if (this.visibility?.hidden) {
      for (const entry of this.entries.values()) {
        if (entry.timer) clearTimeout(entry.timer);
        entry.timer = null;
      }
      return;
    }
    for (const [investigationId, entry] of this.entries) {
      if (entry.listeners.size > 0 && this.shouldPoll(entry)) {
        void this.poll(investigationId);
      }
    }
  };
}
