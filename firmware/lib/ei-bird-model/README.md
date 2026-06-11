# Edge Impulse bird-presence model goes here

This directory holds the **Edge Impulse exported Arduino library** — the on-device
"bird vs. no bird" classifier. It is not committed (it's large and per-deployment);
build it yourself:

1. In [Edge Impulse Studio](https://studio.edgeimpulse.com), start from a public
   **bird sound** project (or clone one) and retrain a 2-class model (`bird` /
   `noise`) on 16 kHz, 1-second windows. Public bird-audio datasets + transfer
   learning get you there fast.
2. **Deployment → Arduino library → Build.** Download the `.zip`.
3. Unzip it into this folder so you end up with `lib/ei-bird-model/src/...` and a
   top-level inferencing header.
4. If your project name isn't `bird`, the header won't be `bird_inferencing.h`.
   Update the `#include` near the top of `firmware/src/main.cpp` to match, and set
   `BIRD_LABEL` in `config.h` to your "bird" class label.

Until a model is present, `firmware/src/main.cpp` compiles with a stubbed
classifier (always 0.0 = "no bird") and prints a build warning — so you can flash
and verify Wi-Fi/NTP/I2S/upload before the model is ready (force an upload by
temporarily lowering `BIRD_SCORE_THRESHOLD` to `-1.0f`).
