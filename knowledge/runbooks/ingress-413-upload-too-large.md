---
title: PDF upload fails with HTTP 413 Request Entity Too Large
type: runbook
owner: platform-team
last_updated: 2026-11-09
tags: [ingress, nginx, upload, 413, pdf, attachments]
---

# PDF upload fails with HTTP 413 Request Entity Too Large

## Symptoms
Uploading a spec sheet with `POST /products/{id}/attachments` returns `413 Request Entity Too Large`. The response is an nginx HTML page, not JSON from the API.

## Likely causes
1. ingress-nginx limits request bodies to 1 MB by default, and the Ingress is missing the body-size annotation.
2. The file really is larger than the API limit of 20 MB (`MAX_UPLOAD_MB`). In that case the 413 comes from the API as JSON with "File larger than 20 MB".

## Diagnose
- If the error body is HTML from nginx, it is the Ingress limit. If it is JSON with a `detail` field, it is the API limit.
- `kubectl describe ingress platform-eng-ingress` and look for `nginx.ingress.kubernetes.io/proxy-body-size`.

## Fix
- Add the annotation `nginx.ingress.kubernetes.io/proxy-body-size: "25m"` to the Ingress and run `kubectl apply -f k8s-local.yaml`.
- For uploads through the MCP server, the AI Ingress needs `40m`, because base64 encoding makes files about a third larger.
- For files over 20 MB, compress the PDF or raise `MAX_UPLOAD_MB` on both the API and the MCP server.

## Prevent
The standard service blueprint requires a body-size annotation on every Ingress that accepts uploads. See postmortem 2026-11 PDF upload outage.
