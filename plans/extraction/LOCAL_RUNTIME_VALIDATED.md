# Local model runtime — built, loaded and validated

**Every gate passes.** Built with CUDA for Blackwell, model loaded onto the GPU, VRAM
allocation visible, health, plain generation and JSON generation all succeeding.

Validated **2026-08-01**. Every figure below is command output.

**No root was needed.** `sudo` requires a password here, so the CUDA toolkit, CMake and
Ninja were installed into a conda environment instead of via `apt`. That is the reproducible
path on this machine and it is what the commands below use.

---

# 1. Recorded configuration

| | |
| --- | --- |
| CUDA toolkit | **13.3.73** (`nvcc`, conda `-c conda-forge -c nvidia`) |
| Driver / GPU | 591.86, **RTX 5060 Ti**, 16,311 MiB |
| Compute capability | 12.0 — Blackwell `sm_120` |
| llama.cpp commit | **`ddd4ec1428a6201e18975ea52b07c71e0f9aef26`** (tag `b10217`) |
| Compiled CUDA architectures | **`120`** — native `sm_120`, not PTX-only |
| `GGML_CUDA` | `ON` |
| GGUF file | **`Qwen3.5-9B-Q4_K_M.gguf`** (`unsloth/Qwen3.5-9B-GGUF`) |
| GGUF SHA-256 | `03b74727a860a56338e042c4420bb3f04b2fec5734175f4cb9fa853daf52b7e8` |
| GGUF size | **5,680,522,464 bytes (5.29 GiB)** |
| Context size | **8,192** (conservative start) |
| GPU layers | `-ngl 99` — all offloaded |
| Flash attention | `-fa on` |
| KV-cache types | `q8_0` / `q8_0` |
| **Idle VRAM** | **305 MiB** |
| **Loaded VRAM** | **5,756 MiB** |
| Delta attributable to the model | **≈ 5,451 MiB** |
| Headroom remaining | ≈ 10.3 GiB |

The delta is the evidence of GPU operation, not the successful build. A CPU-only binary
loads the same model and leaves VRAM at idle; this one moves 5.45 GiB onto the card and
generates at **~67 tokens/s** (247 tokens in 3.69 s), which is an order of magnitude above
CPU inference for a 9B model.

# 2. Commands

## Build

```bash
source /home/thele/miniconda3/etc/profile.d/conda.sh
conda create -y -n fkg-llm python=3.12
conda activate fkg-llm

# --override-channels matters. Mixing `defaults` in makes cuda-toolkit 13.3 unsolvable:
#   nothing provides __win needed by cuda-toolkit-13.3.0-h3a91677_0
# and a libnuma conflict underneath it.
conda install -y --override-channels -c conda-forge -c nvidia \
  "cuda-toolkit>=12.8,<13.4" cmake ninja

export CUDACXX="$CONDA_PREFIX/bin/nvcc"
export PATH="$CONDA_PREFIX/bin:$PATH"
nvcc --version          # gate: must be >= 12.8 for sm_120

git clone --depth 1 https://github.com/ggml-org/llama.cpp ~/llama.cpp
cd ~/llama.cpp
cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=120 \
               -DLLAMA_CURL=OFF -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release -j"$(nproc)"
```

`-DLLAMA_CURL=OFF` avoids needing `libcurl4-openssl-dev`, which would have required root.
The model is fetched with `hf` instead.

## Model

```bash
pip install "huggingface_hub[cli]"
hf download unsloth/Qwen3.5-9B-GGUF Qwen3.5-9B-Q4_K_M.gguf \
  --local-dir ~/models/qwen3.5-9b
sha256sum ~/models/qwen3.5-9b/Qwen3.5-9B-Q4_K_M.gguf
```

## Start

```bash
~/llama.cpp/build/bin/llama-server \
  -m ~/models/qwen3.5-9b/Qwen3.5-9B-Q4_K_M.gguf \
  -ngl 99 -c 8192 --host 127.0.0.1 --port 8080 --jinja \
  -fa on --cache-type-k q8_0 --cache-type-v q8_0
```

## Health check

```bash
curl -s http://127.0.0.1:8080/health                 # {"status":"ok"}
curl -s http://127.0.0.1:8080/v1/models
nvidia-smi --query-gpu=memory.used --format=csv,noheader
```

## Shutdown

```bash
pkill -f 'llama-server -m .*Qwen3.5-9B-Q4_K_M.gguf'
nvidia-smi --query-gpu=memory.used --format=csv,noheader   # expect a return to ~305 MiB
```

# 3. Gate results

| Gate | Result |
| --- | --- |
| `nvcc` supports `sm_120` | **pass** — 13.3.73, architectures `120` |
| llama.cpp compiled with CUDA | **pass** — `GGML_CUDA:BOOL=ON` |
| Model loads | **pass** — `model loaded`, `n_ctx_slot = 8192` |
| GPU layers offloaded | **pass** — `-ngl 99`, VRAM 305 → 5,756 MiB |
| VRAM allocation visible | **pass** — 5,756 MiB while resident |
| HTTP health | **pass** — `{"status":"ok"}` |
| Plain generation | **pass** — 247 tokens in 3.69 s |
| JSON generation | **pass** — schema-conformant, 59 tokens in 1.21 s |

# 4. The finding that changes the narrative lane

**Thinking mode must be off for structured extraction.**

Qwen3.5 is a reasoning model, and with thinking enabled it spends the whole budget
deliberating before emitting anything. With `max_tokens: 900` and a four-field JSON schema:

```text
finish_reason : length
reasoning     : 2,698 characters
content       : ''            <- nothing
```

It was still arguing with itself about whether "metric" meant the label or the value when it
ran out. The same request with thinking disabled:

```json
{"chat_template_kwargs": {"enable_thinking": false}}
```

```text
finish_reason : stop
reasoning     : 0 characters
content       : {"metric_label": "Homes sold", "value": 2946,
                 "unit": "count", "period_end": "March 31, 2025"}
completion    : 59 tokens, 1.21 s
```

Schema-conformant, and it read the correct value from the correct column. **15× fewer
tokens and a usable answer instead of none.** `LocalOpenAICompatibleGenerationProvider` must
send `enable_thinking: false` by default; leaving it on would make the narrative lane
expensive, slow and frequently empty, and the emptiness would present as an extraction
failure rather than a budget one.

Worth revisiting only if a benchmark case turns out to need deliberation — the ambiguous
population-wording cases are the plausible candidates — and then per-request, never globally.

# 5. Notes for the provider implementation

- **`--jinja` is required**, and is set. Without it the model's own chat template is not
  applied and structured output degrades silently.
- The response carries `reasoning_content` alongside `content`. The provider must read
  `content` and may record `reasoning_content` in `extractor_metadata`, which the ontology
  never interprets.
- Prompt caching is active — `cached_tokens: 80` of an 86-token prompt on the second call —
  so a stable prompt prefix across passages will pay off.
- 8,192 context is deliberate. The Q1 2025 KPI table is ~3,000 characters before an
  ontology candidate list is appended; raising the context is a later measurement, not a
  guess.
