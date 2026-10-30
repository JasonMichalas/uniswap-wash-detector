"""Train the weak-label model. Usage: python train_weak.py tokens.txt DB_DIR [OUT_DIR]

tokens.txt has one `name address` per line; each token's cache is DB_DIR/name.duckdb."""

import sys

from washdetect.weak import VOTERS
from washdetect.weakmodel import WEAK_DIR, WeakWashModel, frames_from_db, split_tokens

lines = [line.split() for line in open(sys.argv[1]) if line.strip()]
frames = {name: frames_from_db(name, f"{sys.argv[2]}/{name}.duckdb", token) for name, token in lines}
train, calib = split_tokens(list(frames))
model = WeakWashModel()
model.train([frames[n] for n in train], [frames[n] for n in calib])
print(model.save(sys.argv[3] if len(sys.argv) > 3 else WEAK_DIR), model.manifest)
print(model.wallet_labels.weights(VOTERS).round(3).to_string())
