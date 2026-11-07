# Notice

## The model: YuE2

This app generates music with **YuE2**. It uses these models:

- **YuE2-3B**: https://huggingface.co/m-a-p/YuE2-3B
- **YuE2-Vae**: https://huggingface.co/m-a-p/YuE2-Vae

YuE2 is made by the Multimodal Art Projection (M-A-P) team
(https://github.com/multimodal-art-projection/YuE). The paper is Yuan, Pan, Jiang, Wu, Zhou,
Sun, Li, Zhang *et al.*, "YuE2: Unifying Symbolic and Audio Music Generation at Frontier
Quality", arXiv:2609.33757 (2026), https://arxiv.org/abs/2609.33757. All credit for the
model belongs to its authors.

**This repository and the worker image contain no model weights.** The worker downloads them
from the Hugging Face repos above when it first starts. It installs the inference package,
`yue2_infer`, from the pinned wheel in the `m-a-p/YuE2-3B` repo.

## Licence of the weights: CC BY-NC 4.0

**The licence.** The `LICENSE` in `m-a-p/YuE2-3B` (revision
`8f24312f187763b9854adeec87e437e2b03bbff1`, read 2026-11-07) licenses the YuE2-3B,
YuE2-Vae and YuE2-Vae-legacy weights under the **Creative Commons
Attribution-NonCommercial 4.0 International** licence
(https://creativecommons.org/licenses/by-nc/4.0/). The licence text there is unmodified.

**What that means for this app.** It is meant for **personal, non-commercial use**. Anyone
who wants to use the model or its output commercially should check the licence and ask the
authors.

**Attribution.** As the licence file requests, this notice names YuE2, the model and its
source repositories.

**About an extra permission for creators.** The PRD (and the app's About note) mention an
additional permission that would let individual creators monetise their outputs. That
permission does **not** appear in the pinned `LICENSE` or model card. Do not rely on it
unless you find it in an official statement from the authors.

## Third-party code in the inference package

The `yue2_infer` wheel includes code under the MIT licence from:

- stable-audio-tools
- NVIDIA's SnakeBeta activation

Their notices ship inside the wheel. The `yue2` package code itself states no licence of its
own. Its `LICENSE` covers the weights only.

## This repository

The repository does not have a licence yet. That choice is up to the owner.
