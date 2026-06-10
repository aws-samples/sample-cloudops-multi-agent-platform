/**
 * Visualizer layout + shared constants.
 *
 * Inlines what the source SPA split across `utils/constants.ts` and
 * `utils/shared.ts` — `WELCOME_MESSAGE` is intentionally dropped because the
 * parent chat owns its own greeting.
 */

export const REGION_NAMES: Record<string, string> = {
  'us-east-1': 'US East (N. Virginia)',
  'us-east-2': 'US East (Ohio)',
  'us-west-1': 'US West (N. California)',
  'us-west-2': 'US West (Oregon)',
  'eu-west-1': 'EU (Ireland)',
  'eu-west-2': 'EU (London)',
  'eu-central-1': 'EU (Frankfurt)',
  'ap-southeast-1': 'Asia Pacific (Singapore)',
  'ap-southeast-2': 'Asia Pacific (Sydney)',
  'ap-northeast-1': 'Asia Pacific (Tokyo)',
  'ap-northeast-2': 'Asia Pacific (Seoul)',
  'ap-northeast-3': 'Asia Pacific (Osaka)',
  'ap-south-1': 'Asia Pacific (Mumbai)',
  'sa-east-1': 'South America (Sao Paulo)',
  'ca-central-1': 'Canada (Central)',
  'me-south-1': 'Middle East (Bahrain)',
  'af-south-1': 'Africa (Cape Town)',
};

export const RESILIENCY_TIERS = ['none', 'devtest', 'high', 'maximum'] as const;

export type MockScenario =
  | 'noResiliency'
  | 'devTest'
  | 'high'
  | 'maximum'
  | 'crossAccount'
  | 'cloudWan';

export const LAYOUT = {
  vpcCollapseThreshold: 4,
  tgwCollapseThreshold: 3,
  partnerCollapseThreshold: 3,
};

/**
 * Intrinsic node dimensions consumed by the layout engine. Must approximate
 * the actual rendered size of each node (icon + label + subtitle + badges +
 * padding). If a node overflows its container, bump its entry here.
 */
export const NODE_DIMENSIONS: Record<string, { width: number; height: number }> = {
  onPremise: { width: 200, height: 80 },
  cgw: { width: 260, height: 80 },
  dxPartnerDevice: { width: 170, height: 75 },
  dxPartnerDeviceGroup: { width: 170, height: 90 },
  awsDevice: { width: 170, height: 75 },
  dxGateway: { width: 180, height: 80 },
  tgw: { width: 170, height: 105 },
  tgwConnect: { width: 180, height: 70 },
  vgw: { width: 200, height: 75 },
  vpc: { width: 200, height: 85 },
  vpcGroup: { width: 130, height: 90 },
  tgwGroup: { width: 170, height: 90 },
  isolatedTgwGroup: { width: 170, height: 95 },
  coreNetwork: { width: 260, height: 85 },
};
