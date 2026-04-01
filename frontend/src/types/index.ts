export type ProjectStatus =
  | "created"
  | "uploading"
  | "ready"
  | "migrating"
  | "completed"
  | "failed";

export type RunStatus = "running" | "completed" | "failed";

export type StageStatus =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "skipped";

export interface Project {
  id: number;
  name: string;
  description: string;
  status: ProjectStatus;
  file_count: number;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface MigrationRun {
  id: number;
  project_id: number;
  run_number: number;
  status: RunStatus;
  error_message: string | null;
  procedures_migrated: number;
  procedures_failed: number;
  files_generated: number;
  started_at: string;
  completed_at: string | null;
}

export interface Stage {
  id: number;
  run_id: number;
  stage_name: string;
  stage_order: number;
  status: StageStatus;
  started_at: string | null;
  completed_at: string | null;
  error_message: string | null;
}

export interface ProjectDetail {
  project: Project;
  runs: MigrationRun[];
}

export interface RunDetail {
  run: MigrationRun;
  stages: Stage[];
}

export interface FileInfo {
  name: string;
  size: number;
  lines: number;
}

export interface PersistedLog {
  id: number;
  run_id: number;
  stage: string;
  level: "info" | "warning" | "error";
  message: string;
  created_at: string;
}

export interface LogMessage {
  type: "log" | "complete";
  stage: string;
  message: string;
  level?: "info" | "warning" | "error";
}

export const STAGE_LABELS: Record<string, string> = {
  parse: "Parse T-SQL Files",
  analyze: "Analyze Call Graph",
  plan: "Plan Migration Order",
  extract_schema: "Extract Schema",
  prepare: "Prepare Staging Tables",
  migrate: "Migrate to Java",
  generate: "Generate Output Files",
};
