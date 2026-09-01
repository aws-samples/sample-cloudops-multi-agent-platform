import test from "node:test";
import assert from "node:assert/strict";
import {
  extractInvestigationReference,
  extractInvestigationReferencesFromMemory,
  investigationMarker,
  parseInvestigationMarker,
} from "./investigation-result.ts";
import { PollingInvestigationActivity } from "./investigation-polling.ts";

const sleep = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

function response(id, terminal = false) {
  return {
    investigation: {
      investigationId: id,
      title: "AWS Health: EC2 issue",
      workflowState: terminal ? "COMPLETED" : "RUNNING",
      providerStatus: terminal ? "COMPLETED" : "IN_PROGRESS",
      reason: null,
      createdAt: "2026-01-01T00:00:00Z",
      updatedAt: "2026-01-01T00:01:00Z",
      completedAt: terminal ? "2026-01-01T00:01:00Z" : null,
      terminal,
      providerUrl: null,
    },
    outcome: {
      incident: null,
      rootCause: null,
      mitigation: null,
    },
    activity: [],
    revision: "revision-1",
  };
}

class Visibility {
  hidden = false;
  listeners = [];
  addEventListener(_type, listener) {
    this.listeners.push(listener);
  }
  emit() {
    for (const listener of this.listeners) listener();
  }
}

test("extracts canonical ID from nested Gateway output", () => {
  const result = JSON.stringify({
    content: [{ text: JSON.stringify({ body: JSON.stringify({ investigation: {
      investigationId: "inv-1",
      requestTitle: "AWS Health: EC2 issue",
    } }) }) }],
  });
  assert.deepEqual(
    extractInvestigationReference("gateway___investigate_health_event", result),
    {
      investigationId: "inv-1",
      title: "AWS Health: EC2 issue",
      toolName: "investigate_health_event",
    },
  );
});

test("extracts kickoff result from a nested multi-agent tool trace", () => {
  const investigation = JSON.stringify({
    found: true,
    investigation: {
      investigationId: "inv-nested",
      requestTitle: "AWS Health: Route 53 issue",
    },
  });
  const memory = `<tool>${JSON.stringify({
    name: "ops-excellence-agent",
    output: "Completed investigation",
    tool_trace: [{
      tool_name: "health-events-agent",
      tool_trace: [
        {
          tool_name: "devops-agent___get_health_investigation",
          output: investigation,
        },
        {
          tool_name: "devops-agent___investigate_health_event",
          output: investigation,
        },
      ],
    }],
  })}</tool>`;

  assert.deepEqual(extractInvestigationReferencesFromMemory(memory), [{
    investigationId: "inv-nested",
    title: "AWS Health: Route 53 issue",
    toolName: "investigate_health_event",
  }]);
});

test("extracts an operational alarm investigation kickoff", () => {
  const result = JSON.stringify({
    found: true,
    investigation: {
      investigationId: "inv-alarm",
      requestTitle: "Alarm: High API errors",
    },
  });

  assert.deepEqual(
    extractInvestigationReference(
      "devops-agent___investigate_operational_issue",
      result,
    ),
    {
      investigationId: "inv-alarm",
      title: "Alarm: High API errors",
      toolName: "investigate_operational_issue",
    },
  );
});

test("does not create a card from investigation metadata in a Health listing", () => {
  const output = JSON.stringify({
    events: [{
      eventArn: "arn:aws:health:::event/example",
      investigation: { investigationId: "inv-listing" },
    }],
  });
  assert.equal(
    extractInvestigationReference("health-events___get_recent_events", output),
    null,
  );
});

test("hydrates and deduplicates investigation references from saved tool tags", () => {
  const output = JSON.stringify({ investigation: { investigationId: "inv-1" } });
  const memory = [
    `<tool>${JSON.stringify({ name: "investigate_health_event", output })}</tool>`,
    `<tool>${JSON.stringify({ name: "get_health_investigation", output })}</tool>`,
  ].join("\n");
  const references = extractInvestigationReferencesFromMemory(memory);
  assert.equal(references.length, 1);
  assert.equal(parseInvestigationMarker(investigationMarker(references[0])).investigationId, "inv-1");
});

test("pauses polling while hidden and resumes when visible", async () => {
  const visibility = new Visibility();
  visibility.hidden = true;
  let calls = 0;
  const source = new PollingInvestigationActivity(async () => {
    calls += 1;
    return { notModified: false, etag: '"r1"', data: response("inv-1", true) };
  }, { pollIntervalMs: 1, retryDelaysMs: [1], visibility });
  const unsubscribe = source.subscribe("inv-1", () => {});
  await sleep(5);
  assert.equal(calls, 0);
  visibility.hidden = false;
  visibility.emit();
  await sleep(5);
  assert.equal(calls, 1);
  unsubscribe();
});

test("retries transient failures with bounded backoff", async () => {
  let calls = 0;
  const source = new PollingInvestigationActivity(async () => {
    calls += 1;
    if (calls < 3) throw new Error("transient");
    return { notModified: false, etag: '"r1"', data: response("inv-1", true) };
  }, { pollIntervalMs: 1, retryDelaysMs: [1, 2] });
  const unsubscribe = source.subscribe("inv-1", () => {});
  await sleep(20);
  assert.equal(calls, 3);
  assert.equal(source.getSnapshot("inv-1").status, "ready");
  unsubscribe();
});

test("stops polling after terminal status", async () => {
  let calls = 0;
  const source = new PollingInvestigationActivity(async () => {
    calls += 1;
    return { notModified: false, etag: '"r1"', data: response("inv-1", true) };
  }, { pollIntervalMs: 1, retryDelaysMs: [1] });
  const unsubscribe = source.subscribe("inv-1", () => {});
  await sleep(15);
  assert.equal(calls, 1);
  unsubscribe();
});

test("panel follows mitigation every completed interval until terminal", async () => {
  let calls = 0;
  const source = new PollingInvestigationActivity(async () => {
    calls += 1;
    const data = response("inv-1", true);
    if (calls > 1) {
      data.outcome.mitigation = {
        status: "COMPLETED",
        terminal: true,
        action: "No change required",
        description: "The condition cleared.",
      };
    }
    return { notModified: false, etag: `"r${calls}"`, data };
  }, {
    pollIntervalMs: 1,
    completedPollIntervalMs: 2,
    retryDelaysMs: [1],
  });
  const unsubscribe = source.subscribe(
    "inv-1",
    () => {},
    { followMitigation: true },
  );
  await sleep(20);
  assert.equal(calls, 2);
  unsubscribe();
});

test("panel stops bounded follow-up polling when no mitigation starts", async () => {
  let calls = 0;
  const source = new PollingInvestigationActivity(async () => {
    calls += 1;
    return {
      notModified: false,
      etag: `"r${calls}"`,
      data: response("inv-1", true),
    };
  }, {
    completedPollIntervalMs: 1,
    maxMissingMitigationPolls: 2,
    retryDelaysMs: [1],
  });
  const unsubscribe = source.subscribe(
    "inv-1",
    () => {},
    { followMitigation: true },
  );
  await sleep(15);
  assert.equal(calls, 2);
  unsubscribe();
});

test("manual refresh reconciles with the provider and exposes progress", async () => {
  const calls = [];
  let finishRefresh;
  const source = new PollingInvestigationActivity(
    async (_investigationId, etag, refresh) => {
      calls.push({ etag, refresh });
      if (!refresh) {
        return {
          notModified: false,
          etag: '"initial"',
          data: response("inv-1", true),
        };
      }
      return new Promise((resolve) => {
        finishRefresh = () => {
          const data = response("inv-1", true);
          data.outcome.mitigation = {
            status: "COMPLETED",
            terminal: true,
            action: "Update the launch template",
            description: "The referenced security group was deleted.",
          };
          resolve({ notModified: false, etag: '"refreshed"', data });
        };
      });
    },
  );
  const unsubscribe = source.subscribe("inv-1", () => {});
  await sleep(5);

  source.refresh("inv-1");
  await sleep(1);

  assert.equal(source.getSnapshot("inv-1").refreshing, true);
  assert.deepEqual(calls[1], { etag: undefined, refresh: true });
  finishRefresh();
  await sleep(5);
  assert.equal(source.getSnapshot("inv-1").refreshing, false);
  assert.equal(
    source.getSnapshot("inv-1").data.outcome.mitigation.action,
    "Update the launch template",
  );
  unsubscribe();
});

test("restarts an in-flight poll after immediate resubscription", async () => {
  let calls = 0;
  let finishFirst;
  const firstRequest = new Promise((resolve) => {
    finishFirst = resolve;
  });
  const source = new PollingInvestigationActivity(async () => {
    calls += 1;
    if (calls === 1) return firstRequest;
    return { notModified: false, etag: '"r2"', data: response("inv-1", true) };
  }, { pollIntervalMs: 1, retryDelaysMs: [1] });

  const first = source.subscribe("inv-1", () => {});
  first();
  const second = source.subscribe("inv-1", () => {});
  finishFirst({ notModified: false, etag: '"r1"', data: response("inv-1") });

  await sleep(10);
  assert.equal(calls, 2);
  assert.equal(source.getSnapshot("inv-1").data.investigation.terminal, true);
  second();
});

test("polls multiple active investigation IDs independently", async () => {
  const calls = [];
  const source = new PollingInvestigationActivity(async (id) => {
    calls.push(id);
    return { notModified: false, etag: `"${id}"`, data: response(id, true) };
  }, { pollIntervalMs: 1, retryDelaysMs: [1] });
  const first = source.subscribe("inv-1", () => {});
  const second = source.subscribe("inv-2", () => {});
  await sleep(10);
  assert.deepEqual(new Set(calls), new Set(["inv-1", "inv-2"]));
  first();
  second();
});
