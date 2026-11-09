---
title: Pod stuck in ErrImageNeverPull
type: runbook
owner: platform-team
last_updated: 2026-11-09
tags: [kubernetes, image, docker, errimageneverpull, imagepullpolicy]
---

# Pod stuck in ErrImageNeverPull

## Symptoms
The pod status is `ErrImageNeverPull` and it never starts.

## Likely causes
The Deployment uses `imagePullPolicy: Never`, which tells Kubernetes to use only images already on the node, and the image tag does not exist locally. Usually the image was never built, or it was built with a different tag (for example `lab4-fastapi:v1` instead of `v2`).

## Diagnose
- `docker images | findstr lab4` lists the local images and tags.
- Compare with `image:` in `k8s-local.yaml` or `k8s-ai.yaml`.

## Fix
- Product API: `docker build -t lab4-fastapi:v2 .`
- MCP server and assistant: `docker build -f Dockerfile.ai -t lab4-ai:v1 .`
- Then `kubectl rollout restart deployment <name>`.

## Prevent
Build images with the exact tag the manifest uses. `imagePullPolicy: Never` only works with Docker Desktop's built-in Kubernetes, which shares Docker's image store. On kind or a real cluster, push to a registry instead.
