import type { NodeProps } from '@xyflow/react';
import type { DxNodeData } from '@/lib/topology';
import { BaseNode } from './BaseNode';
import { TransitGatewayIcon } from './aws-icons';

export function TgwConnectNode({ id, data }: NodeProps) {
  const d = data as DxNodeData;
  return (
    <BaseNode
      nodeId={id}
      label={d.label}
      subtitle="Transit Gateway Connect"
      icon={<TransitGatewayIcon />}
      isRecommended={d.isRecommended}
      borderColor="#a78bfa"
      bgColor="#241438"
      badges={d.badges}
    >
      {d.resourceId && (
        <span className="text-[9px] text-slate-500 font-tech">{d.resourceId}</span>
      )}
      {d.details?.state && (
        <span className="text-[9px] text-slate-400 font-tech">{d.details.state}</span>
      )}
    </BaseNode>
  );
}
