from app.mcp.server import build_server

server = build_server()


if __name__ == "__main__":
    server.run(transport="stdio")
