# Local model runtime — prerequisite check

**Verdict: no blocker.** Everything needed for a CUDA-accelerated local `llama.cpp` server
is either present or installable from a reachable repository. Three packages are missing and
none is gated, paid, or unavailable.

Probed on **2026-08-01** on the machine this repository lives on. Every line below is
command output, not inference.

---

# 1. What is present

| Check | Result |
| --- | --- |
| Execution environment | **WSL2**, Ubuntu 24.04, kernel `6.6.87.2-microsoft-standard-WSL2` |
| Windows interop | **Available** — `cmd.exe` reachable from WSL, `WSL_INTEROP` set |
| GPU | **NVIDIA GeForce RTX 5060 Ti** |
| VRAM | **16,311 MiB** (~15.9 GiB), 303 MiB in use, no compute processes |
| Compute capability | **12.0 — `sm_120`, Blackwell** |
| Driver | **591.86**, `nvidia-smi` 590.57 |
| CUDA (driver-reported) | **13.1** |
| Conda | **25.11.1** at `/home/thele/miniconda3/bin/conda` |
| Python | 3.13.11 |
| Free disk | **809 GiB** on `/` (ext4); 94 GiB on `/mnt/c` |
| CPU / RAM | 20 cores / **15 GiB** total |
| Compilers | gcc 13.3.0, g++ 13.3.0, make 4.3, ninja 1.11.1, git 2.43.0 |

Network, all HTTP 200: `huggingface.co`, `pypi.org`, `github.com`,
`developer.download.nvidia.com/compute/cuda/repos/wsl-ubuntu/x86_64/`.

# 2. What is missing

| Missing | Needed for | Source |
| --- | --- | --- |
| `cmake` | building llama.cpp | apt, candidate `3.28.3-1build7` |
| `nvcc` / CUDA toolkit | compiling the CUDA backend | NVIDIA `wsl-ubuntu` repo (reachable) |
| `libcurl4-openssl-dev` | llama.cpp's model downloader | apt |

None is installed. All three are ordinary `apt` installs and nothing else is absent.

# 3. The model

`Qwen/Qwen3.5-9B` **exists and is ungated** — Apache-2.0, 9,653,104,368 parameters,
11.9M downloads. I had doubted the name; the probe settled it.

Two things about it that matter and were not obvious from the name:

**It is a multimodal model.** Architecture `qwen3_5`, pipeline `image-text-to-text`, and the
repo ships `preprocessor_config.json` and `video_preprocessor_config.json`. For claim
extraction we want the text path only; the vision tower is dead weight that a text-only GGUF
quant already drops.

**The base repo has no GGUF.** It is safetensors only, so `llama.cpp` cannot load it
directly. Converting it ourselves is unnecessary — quantized builds are published and
heavily used, which is also the evidence that `llama.cpp` supports the `qwen3_5`
architecture:

| Repo | Downloads |
| --- | --- |
| `unsloth/Qwen3.5-9B-GGUF` | 985,009 |
| `lmstudio-community/Qwen3.5-9B-GGUF` | 479,136 |
| `bartowski/Qwen_Qwen3.5-9B-GGUF` | 51,193 |

## 4-bit quantization options, measured

| File | Size |
| --- | --- |
| `Qwen3.5-9B-IQ4_XS.gguf` | 4.81 GiB |
| `Qwen3.5-9B-IQ4_NL.gguf` | 5.00 GiB |
| `Qwen3.5-9B-Q4_K_S.gguf` | 5.02 GiB |
| **`Qwen3.5-9B-Q4_K_M.gguf`** | **5.29 GiB** |
| `Qwen3.5-9B-Q5_K_M.gguf` | 6.13 GiB |

**Recommendation: `unsloth/Qwen3.5-9B-Q4_K_M.gguf`.** At 5.29 GiB against 15.9 GiB of VRAM
it leaves roughly 10 GiB for KV cache and context, which matters because extraction prompts
carry whole passages — the Q1 2025 KPI table alone is ~3,000 characters and a narrative
lane will send several passages plus an ontology candidate list. Q4_K_M is the standard
quality/size trade; IQ4_XS saves half a gigabyte we do not need to save.

# 4. The one real constraint

**`sm_120` requires CUDA ≥ 12.8.** Blackwell is new enough that older toolkits will not
generate code for it, and a build that silently targets an older architecture will run on
CPU. The driver already reports CUDA 13.1, so the driver side is fine; the toolkit installed
in step 5 must be 12.8 or newer.

Secondary, worth stating: **system RAM is 15 GiB**, not much above the model size. With full
GPU offload (`-ngl 99`) this is comfortable, but a partial-offload configuration would
thrash. Confirm offload actually happened rather than assuming it.

# 5. Two paths, and which to take

## Path A — build llama.cpp with CUDA inside WSL *(recommended)*

Keeps the whole pipeline on one side of the WSL boundary, so the provider is a plain
localhost HTTP call with no interop dependency.

```bash
# 1. Build prerequisites
sudo apt-get update
sudo apt-get install -y cmake libcurl4-openssl-dev

# 2. CUDA toolkit for WSL. MUST be >= 12.8 for sm_120.
wget https://developer.download.nvidia.com/compute/cuda/repos/wsl-ubuntu/x86_64/cuda-keyring_1.1-1_all.deb
sudo dpkg -i cuda-keyring_1.1-1_all.deb
sudo apt-get update
sudo apt-get install -y cuda-toolkit          # pin a >=12.8 version if the default is older
export PATH=/usr/local/cuda/bin:$PATH
nvcc --version                                 # gate: must report >= 12.8

# 3. Build llama.cpp for Blackwell
git clone https://github.com/ggml-org/llama.cpp ~/llama.cpp
cd ~/llama.cpp
cmake -B build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES=120 -DLLAMA_CURL=ON
cmake --build build --config Release -j"$(nproc)"

# 4. Model
conda create -y -n fkg-llm python=3.12
conda activate fkg-llm
pip install "huggingface_hub[cli]"
hf download unsloth/Qwen3.5-9B-GGUF Qwen3.5-9B-Q4_K_M.gguf --local-dir ~/models/qwen3.5-9b

# 5. Serve, OpenAI-compatible
~/llama.cpp/build/bin/llama-server \
  -m ~/models/qwen3.5-9b/Qwen3.5-9B-Q4_K_M.gguf \
  -ngl 99 -c 16384 --host 127.0.0.1 --port 8080 --jinja

# 6. Gates
nvidia-smi --query-gpu=memory.used --format=csv     # expect ~6-7 GiB while loaded
curl -s http://127.0.0.1:8080/v1/models | head
```

`--jinja` matters: structured output needs the model's own chat template applied, and
without it tool/JSON formatting silently degrades.

## Path B — Windows prebuilt CUDA binary over WSL interop *(fallback)*

The upstream **Ubuntu releases ship no CUDA build** — b10216 offers only arm64, openvino,
rocm, sycl and s390x for Linux. Windows does: `llama-b10216-bin-win-cuda-13.3-x64.zip`.
Since interop works, that binary can serve on Windows and be reached from WSL on localhost,
skipping the toolkit install entirely. Take this only if Path A's CUDA install fails — it
adds a Windows/WSL boundary to every request and makes the runtime harder to reproduce on a
non-Windows machine.

# 6. Effect on the plan

**None yet, and none if this is done before step 7.** Steps 3–6 — shared contracts,
evidence resolution, ontology validation, deterministic IDs, typed candidate selection, the
deterministic table lane and its benchmark evaluation — are provider-independent by design
and run fully offline. The narrative lane at step 10 is the first thing that needs a
generation provider.

The gates that a mock cannot satisfy, restated so they are not quietly skipped later:

- GPU acceleration confirmed by VRAM occupancy under load, not by absence of an error;
- measured VRAM figure recorded;
- a structured-output smoke test that returns schema-valid JSON;
- at least one real benchmark case run end to end through the real provider;
- ontology and evidence validation passing on that case's output.
