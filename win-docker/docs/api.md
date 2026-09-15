# Local API specification

The desktop application starts `wincolima api serve --addr 127.0.0.1:38401 --auth-token <random>`. The API is local-only and is not a public remote-management interface.

Every request requires:

```http
Authorization: Bearer <32-or-more-character-session-token>
```

| Method | Path | Result |
| --- | --- | --- |
| `GET` | `/healthz` | API liveness. |
| `GET` | `/v1/status` | Runtime name/state/resources/endpoint. |
| `GET` | `/v1/containers` | Docker container list. |
| `GET` | `/v1/images` | Docker image list. |
| `GET` | `/v1/networks` | Docker networks. |
| `GET` | `/v1/volumes` | Docker volumes. |
| `POST` | `/v1/containers/{id}/start` | Starts a container. |
| `POST` | `/v1/containers/{id}/stop` | Stops a container. |
| `POST` | `/v1/containers/{id}/restart` | Restarts a container. |
| `POST` | `/v1/containers/{id}/delete` | Force-removes a container. |
| `POST` | `/v1/containers/{id}/logs` | Returns the last 500 log lines. |

Responses are JSON. Error bodies have `{ "error": "human-readable reason" }`. Container IDs and names are allow-listed before use. Docker is invoked using argument arrays rather than shell strings.

## gRPC forward contract

The REST API is sufficient for the MVP GUI. The stable, streaming production contract is declared in [`../api/proto/wincolima.proto`](../api/proto/wincolima.proto). Its gRPC server should run over Windows named pipes with mTLS for enterprise remote-control deployments; it should not be exposed on a general LAN socket.
