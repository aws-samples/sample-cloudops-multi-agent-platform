import type { NodeProps } from '@xyflow/react';
import type { DxNodeData } from '@/lib/topology';
import { BaseNode } from './BaseNode';
import { LiveStatusDot } from './LiveStatusDot';
import { AwsDeviceIcon } from './aws-icons';
import { useTopologyStore } from '@/lib/topology/store';

export function AwsDeviceNode({ id, data }: NodeProps) {
  const d = data as DxNodeData;
  const showLiveStatus = useTopologyStore((s) => s.showLiveStatus);
  const state = d.details?.state;
  return (
    <BaseNode
      nodeId={id}
      label={d.label}
      subtitle="AWS Logical Device"
      icon={<AwsDeviceIcon />}
      isRecommended={d.isRecommended}
      accent={d.isInferred ? 'inferred' : 'default'}
      bgColor="#1e1033"
      badges={d.badges}
    >
      {d.details?.logicalDeviceId && d.details.logicalDeviceId !== d.label && (
        <span className="text-[9px] text-slate-400 font-tech">{d.details.logicalDeviceId}</span>
      )}
      {showLiveStatus && <LiveStatusDot state={state} />}
    </BaseNode>
  );
}
