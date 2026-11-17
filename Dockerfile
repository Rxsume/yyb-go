# Build SuperNaiBA/YYB_GO (original) into a Docker image.
# Build context = the SuperNaiBA/YYB_GO repo root (contains yyb_go/).
FROM golang:1.23-bookworm AS build
WORKDIR /src/yyb_go
COPY yyb_go/go.mod yyb_go/go.sum ./
RUN go mod download
COPY yyb_go/ ./
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/yyb-go ./cmd/yyb-go
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates tzdata wget && rm -rf /var/lib/apt/lists/*
ENV TZ=Asia/Shanghai
WORKDIR /app
COPY --from=build /out/yyb-go /app/yyb-go
COPY yyb_go/resource /app/resource
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD wget -q -O /dev/null http://127.0.0.1:8000/health || exit 1
ENTRYPOINT ["/app/yyb-go", "--host", "0.0.0.0", "--port", "8000", "--resource-root", "/app/resource"]
