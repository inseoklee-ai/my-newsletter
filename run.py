import os
import sys

from graph import run

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

if __name__ == "__main__":
    os.environ["DRY_RUN"] = "1" if "--dry-run" in sys.argv else "0"
    out = run()
    for line in out["log"]:
        print(line)
