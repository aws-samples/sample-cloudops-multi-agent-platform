"use client";

import type { NodeProps } from '@xyflow/react';
import type { DxNodeData } from '@/lib/topology';
import { BaseNode } from './BaseNode';
import { LiveStatusDot } from './LiveStatusDot';
import { VpcIcon } from './aws-icons';
import { useTopologyStore } from '@/lib/topology/store';
import { VpcRoutePanel } from './VpcRoutePanel';

export function VpcNode({ id, data }: NodeProps) {
  const d = data as DxNodeData;
  const showLiveStatus = useTopologyStore((s) => s.showLiveStatus);
  const theme = useTopologyStore((s) => s.theme);
  const isExpanded = useTopologyStore((s) => s.expandedVpcRoutePanels.has(d.resourceId ?? ''));
  const togglePanel = useTopologyStore((s) => s.toggleVpcRoutePanel);
  const routeTables = useTopologyStore((s) => s.topologyData?.vpcRouteTables?.get(d.resourceId ?? ''));
  const isCrossAccount = d.details?.crossAccount === 'true';
  const state = d.details?.state;
  const accountColor = theme === 'light' ? '#d97706' : '#fbbf24';
  const hasPeering = d.hasPeeringHandle;
  return (
    <BaseNode
      nodeId={id}
      label={d.label}
      subtitle="Virtual Private Cloud"
      icon={<VpcIcon />}
      isRecommended={d.isRecommended}
      accent={isCrossAccount ? 'crossAccount' : 'default'}
      bgColor="#1e1033"
      badges={d.badges}
      handles={{ source: false, target: true }}
      extraRightHandles={
        hasPeering
          ? [
              { id: 'peering-right', type: 'source', background: '#8b5cf6' },
              { id: 'peering-right-target', type: 'target', background: '#8b5cf6' },
            ]
          : undefined
      }
    >
      {d.resourceId && (
        <span className="text-[9px] text-slate-500 font-tech">{d.resourceId}</span>
      )}
      {d.details?.cidr && (
        <span className="text-[9px] text-slate-400 font-tech">{d.details.cidr}</span>
      )}
      {isCrossAccount && d.details?.ownerAccount && (
        <span className="text-[9px] font-tech whitespace-nowrap" style={{ color: accountColor }}>Account: {d.details.ownerAccount}</span>
      )}
      {showLiveStatus && <LiveStatusDot state={state} />}
      {routeTables && routeTables.length > 0 && (
        <button
          onClick={(e) => { e.stopPropagation(); togglePanel(d.resourceId!); }}
          onPointerDown={(e) => { e.stopPropagation(); e.preventDefault(); }}
          onMouseDown={(e) => { e.stopPropagation(); e.preventDefault(); }}
          className="text-[8px] text-violet-400 hover:text-violet-300 mt-0.5 flex items-center gap-0.5 cursor-pointer self-end nodrag"
          title="View route tables"
        >
          <svg width="10" height="10" viewBox="0 0 16 16" fill="currentColor">
            <path d="M0 2a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H2a2 2 0 0 1-2-2V2zm2 0v3h5V1H2a1 1 0 0 0-1 1zm6-1v4h7V2a1 1 0 0 0-1-1H8zM1 6v4h6V6H1zm7 0v4h7V6H8zM1 11v3a1 1 0 0 0 1 1h5v-4H1zm7 4h6a1 1 0 0 0 1-1v-3H8v4z"/>
          </svg>
          Routes {isExpanded ? '▴' : '▾'}
        </button>
      )}
      {isExpanded && routeTables && (
        <VpcRoutePanel routeTables={routeTables} onClose={() => togglePanel(d.resourceId!)} nodeId={id} />
      )}
    </BaseNode>
  );
}
