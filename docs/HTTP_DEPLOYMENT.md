# Streamable HTTP deployment

The default entry point remains stdio. Start HTTP locally with:

```sh
python -m deploom_mcp --transport streamable-http --host 127.0.0.1 --port 8000
```

Connect an MCP Streamable HTTP client to `http://127.0.0.1:8000/mcp`. DNS-rebinding protection is enabled even when listening on a non-loopback address. By default only loopback Host and Origin headers are allowed. For a gateway with a different Host header, pass `--allowed-host` once per exact host and port (e.g. `--allowed-host mcp.example.com`); for browser Origin headers pass `--allowed-origin https://example.com`. Custom lists replace loopback defaults. Avoid wildcards. The gateway must preserve an allowed Host or rewrite it accordingly.

## Container

Initialize the pinned Core first: `git submodule update --init --recursive`. Build from the MCP repository root:

```sh
docker build -t deploom-mcp:0.2.3 .
docker run --rm -p 127.0.0.1:8000:8000 \
  --mount type=bind,src=/absolute/host/projects,dst=/projects,readonly \
  deploom-mcp:0.2.3
```

Pass `project_path` as a path **inside the container**, e.g. `/projects/my-app`, not the host path. The container user (UID 10001) needs read access. Output and caches are server-side, and disappear when the container is removed unless stored on a suitable writable persistent volume. A read-only project mount supports planning; execution requiring changes needs a separate, deliberately authorized configuration.

The server has no built-in user authentication or per-user path isolation. It exposes planning as well as host-authorized execution tools. All clients share the server's OS identity, project mounts and state; do not mount secrets or unrelated projects. For colleagues use an authenticated TLS gateway with access controls and network restrictions, isolate mutually untrusted users in separate instances and use least-privilege mounts. Do not publish the container port directly. Review host authorization and execution permissions before enabling write access.

Configure client and proxy response/idle timeouts above the chosen `deadline_seconds` and test access to the intended npm registry or proxy from the container. Resource limits and concurrency controls belong to the deployment. The example is a starting point, not validation of a particular Archestra or Kubernetes environment.
