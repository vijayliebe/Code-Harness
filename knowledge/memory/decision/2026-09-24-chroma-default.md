---
type: Decision
title: Default vector backend stays Chroma
description: Keep Chroma until TurboVec recall@k passes the eval suite.
generated: false
verified: human
timestamp: 2026-09-24T00:00:00Z
id: mem/20260924-default-vector-backend-stays-chroma
status: active
supersedes: mem/20260101-use-faiss
tags:
- vector
- chroma
okf_version: "0.2"
x_codeharness:
  kind: memory
  memory_kind: decision
  id: mem/20260924-default-vector-backend-stays-chroma
  status: active
  links:
  - harness/vector_store.py:VectorStore
  supersedes: mem/20260101-use-faiss
---

Keep Chroma as the default vector backend until TurboVec dual-write clears the
eval recall gates (Recall@10 ≥ −2 pts vs Chroma). Code chunks in
`harness/vector_store.py:VectorStore` remain the source of truth for *what the
code does*; this decision answers *what we should do*.
