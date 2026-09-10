import os

_VERSION_PATH = os.path.join(os.path.dirname(__file__), "VERSION")

try:
    with open(_VERSION_PATH) as f:
        __version__ = f.read().strip()
except FileNotFoundError:
    __version__ = "0.0.0-unknown"
