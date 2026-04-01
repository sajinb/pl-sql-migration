import type { ProjectStatus, StageStatus } from "../types";

const STATUS_COLORS: Record<string, string> = {
  created: "var(--pending)",
  uploading: "var(--warning)",
  ready: "var(--primary)",
  migrating: "var(--warning)",
  completed: "var(--success)",
  failed: "var(--error)",
  pending: "var(--pending)",
  running: "var(--warning)",
  skipped: "var(--text-muted)",
};

export default function StatusBadge({
  status,
}: {
  status: ProjectStatus | StageStatus;
}) {
  const color = STATUS_COLORS[status] || "var(--text-muted)";
  return (
    <span
      style={{
        display: "inline-block",
        padding: "2px 10px",
        borderRadius: 12,
        fontSize: 12,
        fontWeight: 600,
        textTransform: "uppercase",
        background: `${color}22`,
        color,
        border: `1px solid ${color}44`,
      }}
    >
      {status}
    </span>
  );
}
