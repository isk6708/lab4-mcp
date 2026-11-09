---
title: Standard service blueprint for Lab 4 platform services
type: blueprint
owner: platform-team
last_updated: 2026-11-09
tags: [blueprint, kubernetes, standards, probes, limits, ingress, secrets]
---

# Standard service blueprint

Every service deployed on the Lab 4 platform follows these rules. AI assistants should recommend this pattern instead of writing raw manifests from scratch, and use `k8s-local.yaml` as the reference implementation.

## Required in every Deployment
- `resources.requests` and `resources.limits` for CPU and memory.
- A readiness probe and a liveness probe on `GET /health`, with `initialDelaySeconds` of at least 3 and 5.
- Configuration through environment variables; credentials only through Kubernetes Secrets (`secretKeyRef` or `envFrom`), never in the image or the manifest.
- Labels: `app: <service-name>`.
- `imagePullPolicy: Never` only on Docker Desktop; use a registry elsewhere.

## Stateful services
- Data on a PersistentVolumeClaim mounted at `/app/data`.
- With SQLite or any ReadWriteOnce volume: `replicas: 1` and `strategy: Recreate`.

## Networking
- A ClusterIP Service on port 80 targeting the container port.
- Exposed through ingress-nginx with `ingressClassName: nginx` and a `<name>.platform.local` host.
- Upload endpoints: `nginx.ingress.kubernetes.io/proxy-body-size` set above the application limit.
- Slow AI endpoints: `proxy-read-timeout: "300"`.

## AI integration
- Expose the service's API to AI through an MCP server that wraps the REST endpoints, not direct database access.
- Tools that change data must require human approval in the assistant.
