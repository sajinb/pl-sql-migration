import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, Link } from "react-router-dom";
import {
  getProject,
  uploadFiles,
  listFiles,
  triggerMigration,
  createMigrationWebSocket,
} from "../api/client";
import FileUpload from "../components/FileUpload";
import LogViewer from "../components/LogViewer";
import StageProgress from "../components/StageProgress";
import StatusBadge from "../components/StatusBadge";
import type { FileInfo, LogMessage, ProjectDetail, Stage } from "../types";

export default function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>();
  const projectId = Number(id);

  const [detail, setDetail] = useState<ProjectDetail | null>(null);
  const [files, setFiles] = useState<FileInfo[]>([]);
  const [logs, setLogs] = useState<LogMessage[]>([]);
  const [activeStage, setActiveStage] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    const d = await getProject(projectId);
    setDetail(d);
    const f = await listFiles(projectId);
    setFiles(f);
  }, [projectId]);

  useEffect(() => {
    load();
  }, [load]);

  // Poll for stage updates while migrating
  useEffect(() => {
    if (detail?.project.status === "migrating") {
      pollRef.current = setInterval(async () => {
        const d = await getProject(projectId);
        setDetail(d);
        // Stop polling when done
        if (d.project.status !== "migrating") {
          if (pollRef.current) clearInterval(pollRef.current);
        }
      }, 2000);
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [detail?.project.status, projectId]);

  const handleUpload = useCallback(
    async (uploadedFiles: File[]) => {
      await uploadFiles(projectId, uploadedFiles);
      await load();
    },
    [projectId, load]
  );

  const handleMigrate = useCallback(async () => {
    setLogs([]);
    setActiveStage(null);

    // Connect WebSocket BEFORE triggering migration
    const ws = createMigrationWebSocket(projectId);
    wsRef.current = ws;

    ws.onmessage = (event) => {
      const msg: LogMessage = JSON.parse(event.data);
      if (msg.type === "log") {
        setLogs((prev) => [...prev, msg]);
        setActiveStage(msg.stage);
      }
      if (msg.type === "complete") {
        load();
      }
    };

    ws.onclose = () => {
      load();
    };

    // Trigger the migration
    try {
      await triggerMigration(projectId);
      await load();
    } catch (err: any) {
      const message = err.response?.data?.detail || err.message;
      setLogs((prev) => [
        ...prev,
        { type: "log", stage: "pipeline", message: `Error: ${message}`, level: "error" },
      ]);
    }
  }, [projectId, load]);

  // Cleanup WebSocket
  useEffect(() => {
    return () => {
      wsRef.current?.close();
    };
  }, []);

  if (!detail) {
    return (
      <div className="container">
        <p style={{ color: "var(--text-muted)" }}>Loading...</p>
      </div>
    );
  }

  const { project, stages } = detail;
  const isMigrating = project.status === "migrating";
  const canMigrate =
    project.file_count > 0 && !isMigrating;

  return (
    <div className="container">
      {/* Header */}
      <div style={{ marginBottom: 24 }}>
        <Link
          to="/"
          style={{ color: "var(--text-muted)", fontSize: 13, display: "block", marginBottom: 8 }}
        >
          &larr; Back to Projects
        </Link>
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
          }}
        >
          <div>
            <h1 style={{ fontSize: 24, fontWeight: 700 }}>{project.name}</h1>
            {project.description && (
              <p style={{ color: "var(--text-muted)", fontSize: 14 }}>
                {project.description}
              </p>
            )}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <StatusBadge status={project.status} />
            <button
              className="btn-primary"
              disabled={!canMigrate}
              onClick={handleMigrate}
              style={{ padding: "10px 24px" }}
            >
              {isMigrating ? "Migrating..." : "Migrate"}
            </button>
          </div>
        </div>
      </div>

      {project.error_message && (
        <div
          className="card"
          style={{
            borderColor: "var(--error)",
            marginBottom: 16,
            background: "var(--error)11",
          }}
        >
          <p style={{ color: "var(--error)", fontSize: 14 }}>
            <strong>Error:</strong> {project.error_message}
          </p>
        </div>
      )}

      {/* File Upload Section */}
      <div className="card" style={{ marginBottom: 24 }}>
        <h2 style={{ fontSize: 16, marginBottom: 16 }}>
          SQL Files ({files.length})
        </h2>

        <FileUpload onUpload={handleUpload} disabled={isMigrating} />

        {files.length > 0 && (
          <div style={{ marginTop: 16 }}>
            <table
              style={{
                width: "100%",
                borderCollapse: "collapse",
                fontSize: 13,
              }}
            >
              <thead>
                <tr style={{ color: "var(--text-muted)", textAlign: "left" }}>
                  <th style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>
                    File
                  </th>
                  <th style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>
                    Lines
                  </th>
                  <th style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>
                    Size
                  </th>
                </tr>
              </thead>
              <tbody>
                {files.map((f) => (
                  <tr key={f.name}>
                    <td style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>
                      {f.name}
                    </td>
                    <td style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>
                      {f.lines.toLocaleString()}
                    </td>
                    <td style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>
                      {(f.size / 1024).toFixed(1)} KB
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Migration Progress Section */}
      {(stages.some((s) => s.status !== "pending") || isMigrating || logs.length > 0) && (
        <div style={{ display: "grid", gridTemplateColumns: "300px 1fr", gap: 24, marginBottom: 24 }}>
          {/* Stage sidebar */}
          <div className="card">
            <h2 style={{ fontSize: 16, marginBottom: 16 }}>Pipeline Stages</h2>
            <StageProgress
              stages={stages}
              activeStage={activeStage}
              onStageClick={setActiveStage}
            />
            {/* "All logs" button */}
            <div style={{ marginTop: 12 }}>
              <button
                className={activeStage === null ? "btn-primary" : "btn-outline"}
                style={{ width: "100%", fontSize: 13 }}
                onClick={() => setActiveStage(null)}
              >
                All Logs
              </button>
            </div>
          </div>

          {/* Log viewer */}
          <div className="card">
            <h2 style={{ fontSize: 16, marginBottom: 16 }}>
              Logs
              {activeStage && (
                <span style={{ color: "var(--text-muted)", fontWeight: 400 }}>
                  {" "}
                  &mdash; {activeStage}
                </span>
              )}
            </h2>
            <LogViewer logs={logs} filterStage={activeStage} />
          </div>
        </div>
      )}
    </div>
  );
}
