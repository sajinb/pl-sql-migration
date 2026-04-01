import { useCallback, useRef, useState } from "react";

interface Props {
  onUpload: (files: File[]) => Promise<void>;
  disabled?: boolean;
}

export default function FileUpload({ onUpload, disabled }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragOver, setDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);

  const handleFiles = useCallback(
    async (fileList: FileList | null) => {
      if (!fileList) return;
      const sqlFiles = Array.from(fileList).filter((f) =>
        f.name.toLowerCase().endsWith(".sql")
      );
      if (sqlFiles.length === 0) return;
      setUploading(true);
      try {
        await onUpload(sqlFiles);
      } finally {
        setUploading(false);
      }
    },
    [onUpload]
  );

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setDragOver(false);
        handleFiles(e.dataTransfer.files);
      }}
      onClick={() => inputRef.current?.click()}
      style={{
        border: `2px dashed ${dragOver ? "var(--primary)" : "var(--border)"}`,
        borderRadius: "var(--radius)",
        padding: 40,
        textAlign: "center",
        cursor: disabled ? "not-allowed" : "pointer",
        opacity: disabled ? 0.5 : 1,
        transition: "border-color 0.15s",
        background: dragOver ? "var(--primary)11" : "transparent",
      }}
    >
      <input
        ref={inputRef}
        type="file"
        accept=".sql"
        multiple
        // @ts-expect-error -- webkitdirectory is non-standard
        webkitdirectory=""
        style={{ display: "none" }}
        onChange={(e) => handleFiles(e.target.files)}
        disabled={disabled}
      />
      {uploading ? (
        <p style={{ color: "var(--warning)" }}>Uploading...</p>
      ) : (
        <>
          <p style={{ fontSize: 16, marginBottom: 8 }}>
            Drag & drop a folder of .sql files here, or click to browse
          </p>
          <p style={{ color: "var(--text-muted)", fontSize: 13 }}>
            Only .sql files will be uploaded
          </p>
        </>
      )}
    </div>
  );
}
