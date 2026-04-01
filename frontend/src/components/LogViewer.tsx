import { useEffect, useRef } from "react";
import type { LogMessage } from "../types";

interface Props {
  logs: LogMessage[];
  filterStage: string | null;
}

const LEVEL_COLORS: Record<string, string> = {
  info: "var(--text)",
  warning: "var(--warning)",
  error: "var(--error)",
};

export default function LogViewer({ logs, filterStage }: Props) {
  const bottomRef = useRef<HTMLDivElement>(null);

  const filtered = filterStage
    ? logs.filter((l) => l.stage === filterStage || l.stage === "pipeline")
    : logs;

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [filtered.length]);

  return (
    <div
      style={{
        background: "#0b1120",
        border: "1px solid var(--border)",
        borderRadius: "var(--radius)",
        padding: 16,
        height: 400,
        overflowY: "auto",
        fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
        fontSize: 13,
        lineHeight: 1.7,
      }}
    >
      {filtered.length === 0 ? (
        <p style={{ color: "var(--text-muted)" }}>
          {filterStage
            ? `No logs for this stage yet...`
            : "Waiting for migration to start..."}
        </p>
      ) : (
        filtered.map((log, i) => (
          <div key={i} style={{ display: "flex", gap: 8 }}>
            <span
              style={{
                color: "var(--text-muted)",
                minWidth: 110,
                fontSize: 11,
                flexShrink: 0,
              }}
            >
              [{log.stage}]
            </span>
            <span style={{ color: LEVEL_COLORS[log.level || "info"] }}>
              {log.message}
            </span>
          </div>
        ))
      )}
      <div ref={bottomRef} />
    </div>
  );
}
