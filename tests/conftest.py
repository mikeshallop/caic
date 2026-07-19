# Ensure the project root is on sys.path so that test modules can import
# top-level packages (app, amqp, cluster, config, …) without PYTHONPATH hacks.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
