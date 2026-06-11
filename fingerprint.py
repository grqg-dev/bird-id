"""Acoustic fingerprints for bird-id clips.

Extracts the BirdNET model's penultimate-layer feature vector (1024 floats) for
a 3s audio window. Unlike the classification output, this vector describes *how
the call sounds*, so two clips can be compared directly (cosine similarity) —
the basis for telling distinct voices of one species apart.

Like `identifier.py`, this module never touches the microphone or the database:
audio file in → vector out. It loads its own copy of the BirdNET TFLite model
because reading an intermediate tensor requires an interpreter created with
`experimental_preserve_all_tensors=True` (birdnetlib's own embeddings path
breaks on current TF without it). The classification interpreter cached in
`identifier.py` is untouched.

Vectors are stored as little-endian float32 BLOBs (see `pack`/`unpack`);
`storage.py` only ever sees bytes, so it stays numpy-free for the test suite.
"""

from __future__ import annotations

# Quiet TensorFlow's startup chatter before it is imported.
import os

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

import warnings
from pathlib import Path

# Bump when the extraction recipe changes (model, padding, layer) so stored
# fingerprints can be told apart from stale ones and re-computed.
FINGERPRINT_VERSION = 1

EMBEDDING_DIM = 1024
SAMPLE_RATE = 48_000          # BirdNET's native input rate
WINDOW_SAMPLES = 144_000      # 3 s × 48 kHz — fixed model input size

# Interpreter is slow to build; cache one per process (same pattern as
# identifier._ANALYZER).
_INTERPRETER = None
_INPUT_INDEX = None
_EMBED_INDEX = None


def _get_interpreter():
    global _INTERPRETER, _INPUT_INDEX, _EMBED_INDEX
    if _INTERPRETER is None:
        from birdnetlib.analyzer import MODEL_PATH
        from tensorflow import lite as tflite

        with warnings.catch_warnings():
            # TF warns that preserve_all_tensors is meant for debugging; we
            # need it to read the embedding layer, and the extra memory is a
            # few dozen MB for this model.
            warnings.simplefilter("ignore", UserWarning)
            _INTERPRETER = tflite.Interpreter(
                model_path=MODEL_PATH,
                num_threads=1,
                experimental_preserve_all_tensors=True,
            )
        _INTERPRETER.allocate_tensors()
        _INPUT_INDEX = _INTERPRETER.get_input_details()[0]["index"]
        # The embedding (GLOBAL_AVG_POOL, 1024 floats) sits one tensor before
        # the classification output — same layout birdnetlib relies on.
        _EMBED_INDEX = _INTERPRETER.get_output_details()[0]["index"] - 1
    return _INTERPRETER


def _embed_samples(y) -> "np.ndarray":
    """Run one 3s window (float32 samples) through the model → 1024-dim vector."""
    import numpy as np

    interp = _get_interpreter()
    y = np.asarray(y, dtype=np.float32)[:WINDOW_SAMPLES]
    if y.size < WINDOW_SAMPLES:
        y = np.pad(y, (0, WINDOW_SAMPLES - y.size))
    interp.set_tensor(_INPUT_INDEX, y[np.newaxis, :])
    interp.invoke()
    return interp.get_tensor(_EMBED_INDEX)[0].copy()


def embed_clip(audio_path: str | Path) -> "np.ndarray":
    """Fingerprint a clip file (first 3 s; shorter audio is zero-padded)."""
    import librosa  # lazy: keeps module import light for tests

    y, _sr = librosa.load(str(audio_path), sr=SAMPLE_RATE, mono=True)
    return _embed_samples(y)


def embed_windows(
    audio_path: str | Path, windows: list[tuple[float, float]]
) -> dict[tuple[float, float], "np.ndarray"]:
    """Fingerprint several (start, end) second-windows of one audio file.

    Loads the file once, so this is the efficient path for a multi-window
    segment wav. Windows past the end of the audio are skipped.
    """
    import librosa

    y, _sr = librosa.load(str(audio_path), sr=SAMPLE_RATE, mono=True)
    out: dict[tuple[float, float], "np.ndarray"] = {}
    for start, end in windows:
        lo = int(start * SAMPLE_RATE)
        if lo >= y.size:
            continue
        out[(start, end)] = _embed_samples(y[lo : lo + WINDOW_SAMPLES])
    return out


def pack(vec) -> bytes:
    """Vector → little-endian float32 bytes for a BLOB column."""
    import numpy as np

    return np.asarray(vec, dtype="<f4").tobytes()


def unpack(blob: bytes) -> "np.ndarray":
    """BLOB bytes → float32 vector."""
    import numpy as np

    return np.frombuffer(blob, dtype="<f4")
