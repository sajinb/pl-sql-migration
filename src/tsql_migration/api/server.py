"""Server entry point — run with: tsql-migrate-server"""

import uvicorn


def main():
    uvicorn.run(
        "tsql_migration.api.app:app",
        host="0.0.0.0",
        port=8001,
        reload=True,
    )


if __name__ == "__main__":
    main()
