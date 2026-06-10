/**
 * Direct Connect visualizer color tokens.
 *
 * Structure preserved 1:1 from the source SPA so node components can reference
 * `COLORS.dark.*` / `COLORS.light.*` paths without rewrites. The `dark` and
 * `light` sub-objects are selected at runtime by node components reading the
 * parent app's `.dark` class on `<html>`.
 *
 * Accent colors (VIF types, severity, status) are intentionally hardcoded —
 * they're semantic/brand, not theme chrome, and should render identically
 * against either canvas.
 */
export const COLORS = {
  existing: {
    border: '#8B5CF6',
    bg: '#F5F3FF',
    text: '#1F2937',
    edge: '#8B5CF6',
  },
  recommended: {
    border: '#10B981',
    bg: '#ECFDF5',
    text: '#065F46',
    edge: '#10B981',
  },
  containers: {
    dxLocation: { border: '#8B5CF6', bg: 'rgba(139,92,246,0.08)' },
    vpc: { border: '#8B5CF6', bg: 'rgba(139,92,246,0.08)' },
    region: { border: '#06B6D4', bg: 'rgba(6,182,212,0.05)' },
  },
  vifTypes: {
    private: '#8B5CF6',
    transit: '#8B5CF6',
    public: '#F97316',
  },
  severity: {
    critical: '#EF4444',
    warning: '#F59E0B',
    info: '#3B82F6',
  },
  status: {
    up: '#22c55e',
    down: '#ef4444',
  },
  dark: {
    surface: '#1e293b',
    surfaceAlt: '#334155',
    border: '#475569',
    text: '#e2e8f0',
    textMuted: '#94a3b8',
    appBg: '#0f172a',
    codeBg: '#0f172a',
    codeHeaderBg: '#1e293b',
  },
  light: {
    border: '#7c3aed',
    edge: '#7c3aed',
    canvasBg: '#eef1f6',
    appBg: '#e4e7ee',
    nodeBg: '#ffffff',
    recommendedBg: '#e8f8f2',
    codeBg: '#1e293b',
    codeHeaderBg: '#334155',
    containerDx: 'rgba(124,58,237,0.08)',
    containerDxHeader: 'rgba(124,58,237,0.14)',
    containerRegion: 'rgba(14,165,233,0.09)',
    containerRegionHeader: 'rgba(14,165,233,0.16)',
    containerOnPrem: 'rgba(100,116,139,0.08)',
    containerOnPremHeader: 'rgba(100,116,139,0.16)',
    containerAwsCloud: 'rgba(71,85,105,0.04)',
    containerAwsCloudHeader: '#334155',
    nodeShadow:
      '0 1px 2px rgba(15,23,42,0.10), 0 3px 8px rgba(15,23,42,0.06), 0 10px 20px rgba(15,23,42,0.04)',
    nodeShadowHover:
      '0 2px 4px rgba(15,23,42,0.12), 0 6px 14px rgba(15,23,42,0.08), 0 16px 28px rgba(15,23,42,0.06)',
    containerShadow: '0 0 0 1px rgba(15,23,42,0.08), 0 1px 3px rgba(15,23,42,0.05)',
  },
};
