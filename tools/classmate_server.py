"""Dedicated server entry: no general launcher and no dotenv loading."""
import argparse
import os
import sys
from pathlib import Path

from classmate_runtime import PORT, ROOT, isolated_env

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--instance", required=True)
    parser.parse_args()
    env = isolated_env(ROOT)
    os.environ.clear()
    os.environ.update(env)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=PORT, workers=1)
