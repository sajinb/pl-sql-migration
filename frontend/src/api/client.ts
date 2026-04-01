import axios from "axios";
import type { Project, ProjectDetail, FileInfo } from "../types";

const API_BASE = import.meta.env.VITE_API_URL || "http://localhost:8000";

const api = axios.create({ baseURL: API_BASE });

export async function listProjects(): Promise<Project[]> {
  const { data } = await api.get("/api/projects");
  return data;
}

export async function createProject(
  name: string,
  description: string
): Promise<Project> {
  const { data } = await api.post("/api/projects", { name, description });
  return data;
}

export async function getProject(id: number): Promise<ProjectDetail> {
  const { data } = await api.get(`/api/projects/${id}`);
  return data;
}

export async function deleteProject(id: number): Promise<void> {
  await api.delete(`/api/projects/${id}`);
}

export async function uploadFiles(
  projectId: number,
  files: File[]
): Promise<Project> {
  const form = new FormData();
  files.forEach((f) => form.append("files", f));
  const { data } = await api.post(`/api/projects/${projectId}/upload`, form);
  return data;
}

export async function listFiles(projectId: number): Promise<FileInfo[]> {
  const { data } = await api.get(`/api/projects/${projectId}/files`);
  return data;
}

export async function triggerMigration(projectId: number): Promise<Project> {
  const { data } = await api.post(`/api/projects/${projectId}/migrate`);
  return data;
}

export function createMigrationWebSocket(projectId: number): WebSocket {
  const wsBase = API_BASE.replace(/^http/, "ws");
  return new WebSocket(`${wsBase}/api/projects/${projectId}/ws`);
}
