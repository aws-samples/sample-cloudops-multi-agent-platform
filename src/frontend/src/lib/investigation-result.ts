export const INVESTIGATION_TOOL_NAMES = new Set([
  "investigate_health_event",
  "investigate_operational_issue",
  "get_health_investigation",
]);

export const INVESTIGATION_KICKOFF_TOOL_NAMES = new Set([
  "investigate_health_event",
  "investigate_operational_issue",
]);

export interface InvestigationReference {
  investigationId: string;
  title: string;
  toolName: string;
}

const MARKER_RE = /^<investigation-ref>([\s\S]+)<\/investigation-ref>$/;

function bareToolName(name: string): string {
  return name.includes("___") ? name.split("___").pop() || name : name;
}

function peelJson(value: unknown): unknown {
  let current = value;
  for (let depth = 0; depth < 5 && typeof current === "string"; depth += 1) {
    try {
      current = JSON.parse(current);
    } catch {
      break;
    }
  }
  return current;
}

function stringValue(value: unknown): string | undefined {
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function fallbackTitle(toolName: string): string {
  return toolName === "investigate_health_event"
    ? "AWS Health investigation"
    : "DevOps Agent investigation";
}

function referenceFromPayload(
  toolName: string,
  result: unknown,
): InvestigationReference | null {
  const seen = new WeakSet<object>();
  let found: InvestigationReference | null = null;

  const walk = (value: unknown): void => {
    if (found) return;
    const node = peelJson(value);
    if (!node || typeof node !== "object") return;
    if (seen.has(node as object)) return;
    seen.add(node as object);

    if (Array.isArray(node)) {
      for (const item of node) walk(item);
      return;
    }

    const object = node as Record<string, unknown>;
    const investigation =
      object.investigation && typeof object.investigation === "object"
        ? (object.investigation as Record<string, unknown>)
        : object;
    const investigationId =
      stringValue(investigation.investigationId) ||
      stringValue(investigation.investigation_id);
    if (investigationId) {
      found = {
        investigationId,
        title:
          stringValue(investigation.requestTitle) ||
          stringValue(investigation.title) ||
          fallbackTitle(toolName),
        toolName,
      };
      return;
    }

    for (const child of Object.values(object)) walk(child);
  };

  walk(result);
  return found;
}

export function extractInvestigationReferences(
  toolName: string,
  result: unknown,
): InvestigationReference[] {
  const references = new Map<string, InvestigationReference>();

  const add = (reference: InvestigationReference | null): void => {
    if (!reference) return;
    const current = references.get(reference.investigationId);
    if (!current || INVESTIGATION_KICKOFF_TOOL_NAMES.has(reference.toolName)) {
      references.set(reference.investigationId, reference);
    }
  };

  const outerName = bareToolName(toolName);
  if (INVESTIGATION_TOOL_NAMES.has(outerName)) {
    add(referenceFromPayload(outerName, result));
  }

  const seen = new WeakSet<object>();
  const walk = (value: unknown): void => {
    const node = peelJson(value);
    if (!node || typeof node !== "object") return;
    if (seen.has(node as object)) return;
    seen.add(node as object);

    if (Array.isArray(node)) {
      for (const item of node) walk(item);
      return;
    }

    const object = node as Record<string, unknown>;
    const nestedName = bareToolName(
      stringValue(object.tool_name) || stringValue(object.name) || "",
    );
    if (INVESTIGATION_TOOL_NAMES.has(nestedName)) {
      add(
        referenceFromPayload(
          nestedName,
          object.output ?? object.result ?? object.response ?? object,
        ),
      );
    }
    for (const child of Object.values(object)) walk(child);
  };

  walk(result);
  return [...references.values()].sort((left, right) => {
    if (left.toolName === right.toolName) return 0;
    return INVESTIGATION_KICKOFF_TOOL_NAMES.has(left.toolName) ? -1 : 1;
  });
}

export function extractInvestigationReference(
  toolName: string,
  result: unknown,
): InvestigationReference | null {
  return extractInvestigationReferences(toolName, result)[0] || null;
}

export function investigationMarker(reference: InvestigationReference): string {
  return `<investigation-ref>${JSON.stringify(reference)}</investigation-ref>`;
}

export function parseInvestigationMarker(text: string): InvestigationReference | null {
  const match = text.match(MARKER_RE);
  if (!match) return null;
  try {
    const parsed = JSON.parse(match[1]) as InvestigationReference;
    if (!stringValue(parsed.investigationId)) return null;
    return {
      investigationId: parsed.investigationId,
      title:
        stringValue(parsed.title) ||
        fallbackTitle(bareToolName(stringValue(parsed.toolName) || "")),
      toolName: bareToolName(stringValue(parsed.toolName) || "get_health_investigation"),
    };
  } catch {
    return null;
  }
}

export function extractInvestigationReferencesFromMemory(
  enrichedText: string,
): InvestigationReference[] {
  if (!enrichedText.includes("<tool>")) return [];
  const references = new Map<string, InvestigationReference>();
  const toolRe = /<tool>([\s\S]*?)<\/tool>/g;
  let match: RegExpExecArray | null;
  while ((match = toolRe.exec(enrichedText)) !== null) {
    try {
      const tool = JSON.parse(match[1]) as Record<string, unknown>;
      const name = stringValue(tool.name) || stringValue(tool.tool_name) || "";
      for (const reference of extractInvestigationReferences(name, tool)) {
        const current = references.get(reference.investigationId);
        if (!current || INVESTIGATION_KICKOFF_TOOL_NAMES.has(reference.toolName)) {
          references.set(reference.investigationId, reference);
        }
      }
    } catch {
      // Ignore malformed historical tool records.
    }
  }
  return [...references.values()];
}
