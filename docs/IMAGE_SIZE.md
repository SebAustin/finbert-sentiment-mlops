# Docker image size: before / after

Both images were built for `linux/amd64` (the Fargate platform) on 2026-10-08 with
Docker 29.8 and contain the same API code plus the baked-in FinBERT model.

| Image | Dockerfile | Torch wheel | Size on disk¹ | `docker images`² |
|---|---|---|---|---|
| **Before**: `finbert-api:baseline` | [`docs/Dockerfile.baseline`](Dockerfile.baseline) | PyPI default (CUDA 12 libs) + full `requirements.txt`, pip cache kept, `build-essential` | **11 GB** | 7.51 GB |
| **After**: `finbert-api:cpu` | [`Dockerfile`](../Dockerfile) | `torch==2.5.1+cpu` via `requirements-prod.txt`, `--no-cache-dir` | **1.8 GB** | 0.78 GB |

¹ `du -sh /` inside the container (unpacked filesystem).
² Size shown by Docker Desktop's containerd image store right after the build (content size).

**Result: about 6× smaller on disk (11 GB → 1.8 GB), and about 10× less to push and pull.**

Where the savings come from (baseline breakdown):

| Item | Baseline | Optimised |
|---|---|---|
| `site-packages` | 6.7 GB (CUDA/cuDNN/NCCL wheels, deepchecks, DVC, locust, …) | 1.3 GB (runtime deps only) |
| pip cache (`/root/.cache/pip`) | 3.7 GB | 0 (`PIP_NO_CACHE_DIR=1`, `--no-cache-dir`) |
| Model (`HF_HOME`) | ~0.4 GB | 0.42 GB (single layer)³ |
| Compiler toolchain | build-essential | none needed |

³ A first version of the optimised Dockerfile ran `chown -R appuser /app` *after* baking the
model. That copies the 440 MB model into a second layer. Creating the files as the runtime
user from the start (`USER appuser` + `COPY --chown`) removed the duplicate.

Reproduce:

```bash
docker build --platform linux/amd64 -t finbert-api:cpu .
docker build --platform linux/amd64 -f docs/Dockerfile.baseline -t finbert-api:baseline .
docker run --rm --entrypoint sh finbert-api:cpu -c 'du -sh / --exclude=/proc --exclude=/sys'
```
