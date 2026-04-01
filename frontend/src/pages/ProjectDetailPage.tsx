import { useCallback, useEffect, useRef, useState } from "react";
import { useParams, Link } from "react-router-dom";
import {
  getProject,
  uploadFiles,
  listFiles,
  triggerMigration,
  getRun,
  getRunLogs,
  createRunWebSocket,
} from "../api/client";
import FileUpload from "../components/FileUpload";
import LogViewer from "../components/LogViewer";
import StageProgress from "../components/StageProgress";
import StatusBadge from "../components/StatusBadge";
import type {
  FileInfo,
  LogMessage,
  MigrationRun,
  ProjectDetail,
  Stage,
} from "../types";

export default function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>();
  const projectId = Number(id);

  const [detail, setDetail] = useState<ProjectDetail | null>(null);
  const [files, setFiles] = useState<FileInfo[]>([]);

  // Active run tracking
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [stages, setStages] = useState<Stage[]>([]);
  const [logs, setLogs] = useState<LogMessage[]>([]);
  const [activeStage, setActiveStage] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    const d = await getProject(projectId);
    setDetail(d);
    const f = await listFiles(projectId);
    setFiles(f);
    return d;
  }, [projectId]);

  useEffect(() => {
    load().then((d) => {
      // Auto-select the latest run
      if (d.runs.length > 0 && !selectedRunId) {
        selectRun(d.runs[0]);
      }
    });
  }, [load]);

  // Load run stages and logs when a run is selected
  const selectRun = useCallback(
    async (run: MigrationRun) => {
      setSelectedRunId(run.id);
      setActiveStage(null);

      const runDetail = await getRun(projectId, run.id);
      setStages(runDetail.stages);

      // Load persisted logs
      const persistedLogs = await getRunLogs(projectId, run.id);
      setLogs(
        persistedLogs.map((l) => ({
          type: "log" as const,
          stage: l.stage,
          message: l.message,
          level: l.level,
        }))
      );

      // If the run is still active, connect WebSocket for live updates
      if (run.status === "running") {
        connectWebSocket(run.id);
        startPolling(run.id);
      }
    },
    [projectId]
  );

  const connectWebSocket = useCallback(
    (runId: number) => {
      wsRef.current?.close();
      const ws = createRunWebSocket(projectId, runId);
      wsRef.current = ws;

      ws.onmessage = (event) => {
        const msg: LogMessage = JSON.parse(event.data);
        if (msg.type === "log") {
          setLogs((prev) => [...prev, msg]);
          setActiveStage(msg.stage);
        }
        if (msg.type === "complete") {
          stopPolling();
          load();
        }
      };

      ws.onclose = () => {
        stopPolling();
        load();
      };
    },
    [projectId, load]
  );

  const startPolling = useCallback(
    (runId: number) => {
      stopPolling();
      pollRef.current = setInterval(async () => {
        const runDetail = await getRun(projectId, runId);
        setStages(runDetail.stages);
        if (runDetail.run.status !== "running") {
          stopPolling();
          load();
        }
      }, 2000);
    },
    [projectId, load]
  );

  const stopPolling = useCallback(() => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

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
    setStages([]);

    try {
      const run = await triggerMigration(projectId);
      setSelectedRunId(run.id);

      // Reload project to get updated runs list
      await load();

      // Load stages for the new run
      const runDetail = await getRun(projectId, run.id);
      setStages(runDetail.stages);

      // Connect WebSocket for live streaming
      connectWebSocket(run.id);
      startPolling(run.id);
    } catch (err: any) {
      const message = err.response?.data?.detail || err.message;
      setLogs((prev) => [
        ...prev,
        {
          type: "log",
          stage: "pipeline",
          message: `Error: ${message}`,
          level: "error",
        },
      ]);
    }
  }, [projectId, load, connectWebSocket, startPolling]);

  // Cleanup
  useEffect(() => {
    return () => {
      wsRef.current?.close();
      stopPolling();
    };
  }, [stopPolling]);

  if (!detail) {
    return (
      <div className="container">
        <p style={{ color: "var(--text-muted)" }}>Loading...</p>
      </div>
    );
  }

  const { project, runs } = detail;
  const isMigrating = project.status === "migrating";
  const canMigrate = project.file_count > 0 && !isMigrating;
  const selectedRun = runs.find((r) => r.id === selectedRunId);

  return (
    <div className="container">
      {/* Header */}
      <div style={{ marginBottom: 24 }}>
        <Link
          to="/"
          style={{
            color: "var(--text-muted)",
            fontSize: 13,
            display: "block",
            marginBottom: 8,
          }}
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
              {isMigrating ? "Migrating..." : runs.length > 0 ? "Re-run Migration" : "Migrate"}
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
                  <th style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>File</th>
                  <th style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>Lines</th>
                  <th style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>Size</th>
                </tr>
              </thead>
              <tbody>
                {files.map((f) => (
                  <tr key={f.name}>
                    <td style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>{f.name}</td>
                    <td style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>{f.lines.toLocaleString()}</td>
                    <td style={{ padding: "8px 12px", borderBottom: "1px solid var(--border)" }}>{(f.size / 1024).toFixed(1)} KB</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Run History */}
      {runs.length > 0 && (
        <div className="card" style={{ marginBottom: 24 }}>
          <h2 style={{ fontSize: 16, marginBottom: 16 }}>
            Migration Runs ({runs.length})
          </h2>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {runs.map((run) => (
              <div
                key={run.id}
                onClick={() => selectRun(run)}
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  padding: "10px 16px",
                  borderRadius: "var(--radius)",
                  cursor: "pointer",
                  background:
                    selectedRunId === run.id
                      ? "var(--bg-input)"
                      : "transparent",
                  border:
                    selectedRunId === run.id
                      ? "1px solid var(--primary)"
                      : "1px solid var(--border)",
                  transition: "all 0.15s",
                }}
              >
                <div>
                  <span style={{ fontWeight: 600, fontSize: 14 }}>
                    Run #{run.run_number}
                  </span>
                  <span
                    style={{
                      color: "var(--text-muted)",
                      fontSize: 12,
                      marginLeft: 12,
                    }}
                  >
                    {new Date(run.started_at).toLocaleString()}
                  </span>
                  {run.status === "completed" && (
                    <span
                      style={{
                        color: "var(--text-muted)",
                        fontSize: 12,
                        marginLeft: 12,
                      }}
                    >
                      {run.procedures_migrated} migrated, {run.procedures_failed} failed, {run.files_generated} files
                    </span>
                  )}
                </div>
                <StatusBadge status={run.status} />
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Pipeline Progress + Logs for selected run */}
      {selectedRun && stages.length > 0 && (
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "300px 1fr",
            gap: 24,
            marginBottom: 24,
          }}
        >
          {/* Stage sidebar */}
          <div className="card">
            <h2 style={{ fontSize: 16, marginBottom: 16 }}>
              Run #{selectedRun.run_number} Stages
            </h2>
            <StageProgress
              stages={stages}
              activeStage={activeStage}
              onStageClick={setActiveStage}
            />
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
                <span
                  style={{ color: "var(--text-muted)", fontWeight: 400 }}
                >
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
