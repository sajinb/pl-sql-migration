import type { Stage } from "../types";
import { STAGE_LABELS } from "../types";
import StatusBadge from "./StatusBadge";

interface Props {
  stages: Stage[];
  activeStage: string | null;
  onStageClick: (stageName: string) => void;
}

export default function StageProgress({ stages, activeStage, onStageClick }: Props) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      {stages.map((stage) => {
        const isActive = activeStage === stage.stage_name;
        return (
          <div
            key={stage.stage_name}
            onClick={() => onStageClick(stage.stage_name)}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "space-between",
              padding: "10px 16px",
              borderRadius: "var(--radius)",
              cursor: "pointer",
              background: isActive ? "var(--bg-input)" : "transparent",
              border: isActive
                ? "1px solid var(--primary)"
                : "1px solid transparent",
              transition: "all 0.15s",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <StageIcon status={stage.status} />
              <span style={{ fontSize: 14 }}>
                {STAGE_LABELS[stage.stage_name] || stage.stage_name}
              </span>
            </div>
            <StatusBadge status={stage.status} />
          </div>
        );
      })}
    </div>
  );
}

function StageIcon({ status }: { status: string }) {
  const size = 24;
  const common = { width: size, height: size, display: "flex", alignItems: "center", justifyContent: "center" };

  if (status === "running") {
    return (
      <div style={{ ...common }}>
        <div
          style={{
            width: 16,
            height: 16,
            border: "2px solid var(--warning)",
            borderTopColor: "transparent",
            borderRadius: "50%",
            animation: "spin 0.8s linear infinite",
          }}
        />
        <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
      </div>
    );
  }

  if (status === "completed") {
    return (
      <div style={{ ...common, color: "var(--success)", fontSize: 18 }}>
        &#10003;
      </div>
    );
  }

  if (status === "failed") {
    return (
      <div style={{ ...common, color: "var(--error)", fontSize: 18 }}>
        &#10007;
      </div>
    );
  }

  return (
    <div
      style={{
        ...common,
        width: 16,
        height: 16,
        borderRadius: "50%",
        border: "2px solid var(--pending)",
        marginLeft: 4,
      }}
    />
  );
}
