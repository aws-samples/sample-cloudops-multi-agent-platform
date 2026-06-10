import type { TopologyData } from './topology-types';

/**
 * Count *distinct AWS logical devices* per DX location.
 *
 * Max tier (99.99%) requires 2+ different AWS routers at each location — two
 * connections sharing one `awsLogicalDeviceId` terminate on the same physical
 * device and don't survive a device failure. When the logical ID is missing
 * (sometimes true for hosted VIFs), fall back to the connection / VIF ID so
 * each raw entry still counts once — we err toward the more generous read
 * when AWS doesn't expose device identity.
 *
 * Single source of truth for "how many redundant devices does location X
 * have". Every place that gates on the Max SLA (tier determination, ghost-node
 * rules, scorecard UI, HTML report, cost estimator) should call this helper
 * rather than counting raw `topology.connections`.
 */
export function getLocationDeviceCounts(topology: TopologyData): Map<string, number> {
  const locationDevices = new Map<string, Set<string>>();

  const addDevice = (loc: string, deviceKey: string) => {
    if (!loc) return;
    let set = locationDevices.get(loc);
    if (!set) {
      set = new Set();
      locationDevices.set(loc, set);
    }
    set.add(deviceKey);
  };

  if (topology.connections.length > 0) {
    for (const conn of topology.connections) {
      const vif = topology.virtualInterfaces.find((v) => v.connectionId === conn.connectionId);
      const deviceKey = conn.awsLogicalDeviceId || vif?.awsLogicalDeviceId || conn.connectionId;
      addDevice(conn.location, deviceKey);
    }
  } else {
    for (const vif of topology.virtualInterfaces) {
      const deviceKey = vif.awsLogicalDeviceId || vif.connectionId || vif.virtualInterfaceId;
      addDevice(vif.location ?? '', deviceKey);
    }
  }

  const counts = new Map<string, number>();
  for (const [loc, set] of locationDevices) counts.set(loc, set.size);
  return counts;
}
