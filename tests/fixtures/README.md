# Test audio fixtures

Tracked bird recordings for local development and optional BirdNET integration
smokes. CI does **not** run BirdNET — these files are for manual checks only.

| File | Duration | Expected top hit | Notes |
|------|----------|------------------|-------|
| `bewicks_wren.wav` | ~2.75 s | Bewick's Wren (~0.92 @ `-c 0.1`) | 44.1 kHz mono PCM; copied from prod mini `fixtures/bird.wav` |

## Quick smoke (requires full runtime venv + TensorFlow)

```bash
./.venv/bin/python birdid.py identify tests/fixtures/bewicks_wren.wav -c 0.1
```

Do not pass `--save` during routine smokes — that writes to your local `birdid.db`.
