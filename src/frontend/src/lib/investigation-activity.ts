"use client";

import { useCallback, useSyncExternalStore } from "react";
import { getToken } from "./auth";
import { getInvestigationActivity } from "./runtime-client";
import {
  PollingInvestigationActivity,
  type InvestigationActivityState,
  type InvestigationSubscriptionOptions,
  type InvestigationActivitySubscription,
} from "./investigation-polling";

const EMPTY_STATE: InvestigationActivityState = { status: "idle" };

let activitySubscription: InvestigationActivitySubscription =
  new PollingInvestigationActivity((investigationId, etag, refresh) =>
    getInvestigationActivity(investigationId, getToken, etag, refresh));

export function setInvestigationActivitySubscription(
  subscription: InvestigationActivitySubscription,
): void {
  activitySubscription = subscription;
}

export function useInvestigationActivity(
  investigationId: string,
  options: InvestigationSubscriptionOptions = {},
) {
  const source = activitySubscription;
  const followMitigation = Boolean(options.followMitigation);
  const subscribe = useCallback(
    (listener: () => void) => source.subscribe(
      investigationId,
      listener,
      { followMitigation },
    ),
    [followMitigation, investigationId, source],
  );
  const getSnapshot = useCallback(
    () => source.getSnapshot(investigationId),
    [investigationId, source],
  );
  const state = useSyncExternalStore(subscribe, getSnapshot, () => EMPTY_STATE);
  const refresh = useCallback(
    () => source.refresh(investigationId),
    [investigationId, source],
  );
  return { ...state, refresh };
}
