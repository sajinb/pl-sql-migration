import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { listProjects, createProject, deleteProject } from "../api/client";
import StatusBadge from "../components/StatusBadge";
import type { Project } from "../types";

export default function ProjectListPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [loading, setLoading] = useState(true);
  const navigate = useNavigate();

  const load = async () => {
    setLoading(true);
    try {
      setProjects(await listProjects());
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const handleCreate = async () => {
    if (!name.trim()) return;
    const project = await createProject(name.trim(), description.trim());
    setShowCreate(false);
    setName("");
    setDescription("");
    navigate(`/projects/${project.id}`);
  };

  const handleDelete = async (id: number) => {
    if (!confirm("Delete this project and all its files?")) return;
    await deleteProject(id);
    load();
  };

  return (
    <div className="container">
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 24,
        }}
      >
        <h1 style={{ fontSize: 28, fontWeight: 700 }}>Projects</h1>
        <button className="btn-primary" onClick={() => setShowCreate(true)}>
          + New Project
        </button>
      </div>

      {showCreate && (
        <div className="card" style={{ marginBottom: 24 }}>
          <h2 style={{ fontSize: 18, marginBottom: 16 }}>Create Project</h2>
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <input
              placeholder="Project name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              autoFocus
            />
            <textarea
              placeholder="Description (optional)"
              rows={2}
              value={description}
              onChange={(e) => setDescription(e.target.value)}
            />
            <div style={{ display: "flex", gap: 8 }}>
              <button className="btn-primary" onClick={handleCreate}>
                Create
              </button>
              <button
                className="btn-outline"
                onClick={() => setShowCreate(false)}
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}

      {loading ? (
        <p style={{ color: "var(--text-muted)" }}>Loading...</p>
      ) : projects.length === 0 ? (
        <div
          className="card"
          style={{ textAlign: "center", padding: 48, color: "var(--text-muted)" }}
        >
          <p>No projects yet. Click "New Project" to get started.</p>
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          {projects.map((p) => (
            <div
              key={p.id}
              className="card"
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "center",
              }}
            >
              <div>
                <Link
                  to={`/projects/${p.id}`}
                  style={{
                    fontSize: 16,
                    fontWeight: 600,
                    color: "var(--text)",
                  }}
                >
                  {p.name}
                </Link>
                {p.description && (
                  <p
                    style={{
                      color: "var(--text-muted)",
                      fontSize: 13,
                      marginTop: 4,
                    }}
                  >
                    {p.description}
                  </p>
                )}
                <p
                  style={{
                    color: "var(--text-muted)",
                    fontSize: 12,
                    marginTop: 4,
                  }}
                >
                  {p.file_count} SQL files &middot; Created{" "}
                  {new Date(p.created_at).toLocaleDateString()}
                </p>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <StatusBadge status={p.status} />
                <button
                  className="btn-danger"
                  style={{ fontSize: 12, padding: "4px 10px" }}
                  onClick={(e) => {
                    e.stopPropagation();
                    handleDelete(p.id);
                  }}
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
