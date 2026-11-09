---
title: Pod in CrashLoopBackOff
type: runbook
owner: platform-team
last_updated: 2026-11-09
tags: [kubernetes, pod, crashloopbackoff, restart, probes]
---

# Pod in CrashLoopBackOff

## Symptoms
`kubectl get pods` shows the pod as `CrashLoopBackOff` or `Error`, with a rising RESTARTS count. The service returns HTTP 503 through the Ingress because no pod is Ready.

## Likely causes
1. The application exits on startup: a Python import error, a missing package, or a bad environment variable such as an invalid `DATABASE_URL`.
2. The container cannot write to `/app/data` because the volume is missing or read-only.
3. The liveness probe fails before the app has started, so Kubernetes keeps killing it. The Lab 4 API needs about 3 seconds to start.
4. The container runs out of memory and is `OOMKilled` (limit is 256Mi for the API).

## Diagnose
- `kubectl logs <pod> --previous` shows the output of the crashed container, which is where the real error is.
- `kubectl describe pod <pod>` shows the last state, exit code and events. Exit code 137 means OOMKilled.
- Check that the secret exists: `kubectl get secret platform-local-secrets`.

## Fix
- Import or package errors: fix `requirements.txt`, rebuild the image with `docker build -t lab4-fastapi:v2 .`, then `kubectl rollout restart deployment platform-eng-app`.
- Bad settings: correct the Secret or env values in `k8s-local.yaml` and re-apply.
- Probe too aggressive: raise `initialDelaySeconds` on the liveness probe.
- OOMKilled: raise the memory limit, for example to 512Mi, and look for large PDF uploads being processed.

## Prevent
Follow the standard service blueprint: readiness and liveness probes on `/health`, sensible start delays, and explicit memory limits.
