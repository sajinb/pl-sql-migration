import { Link } from "react-router-dom";

export default function Header() {
  return (
    <header
      style={{
        borderBottom: "1px solid var(--border)",
        padding: "16px 0",
        marginBottom: 32,
      }}
    >
      <div className="container" style={{ display: "flex", alignItems: "center", gap: 16 }}>
        <Link to="/" style={{ fontSize: 20, fontWeight: 700, color: "var(--text)" }}>
          T-SQL Migration Tool
        </Link>
        <span style={{ color: "var(--text-muted)", fontSize: 14 }}>
          T-SQL to Spring Boot 3 / Java 21
        </span>
      </div>
    </header>
  );
}
