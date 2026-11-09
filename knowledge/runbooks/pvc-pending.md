---
title: Pod Pending because the PersistentVolumeClaim is not bound
type: runbook
owner: platform-team
last_updated: 2026-11-09
tags: [kubernetes, storage, pvc, pending, volume]
---

# Pod Pending because the PersistentVolumeClaim is not bound

## Symptoms
The API pod stays `Pending`. `kubectl get pvc lab4-data` shows `Pending` instead of `Bound`.

## Likely causes
1. The cluster has no default StorageClass. Docker Desktop provides `hostpath` by default; kind provides `standard`.
2. An old pod still holds the ReadWriteOnce volume during a rolling update.

## Diagnose
- `kubectl get storageclass` and look for one marked `(default)`.
- `kubectl describe pvc lab4-data` shows the provisioning events.

## Fix
- Missing StorageClass: set `storageClassName` in the PVC to one that exists.
- Volume held by an old pod: the Deployment uses `strategy: Recreate` so the old pod stops first. Check that it has not been changed to RollingUpdate.

## Prevent
Keep `replicas: 1` and `strategy: Recreate` while the API uses SQLite on a ReadWriteOnce volume. Move to PostgreSQL before scaling out.
