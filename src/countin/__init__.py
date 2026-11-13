import os

from .core.io import resource

# Bundled ffmpeg goes first on PATH for anything that looks it up by name.
os.environ["PATH"] = str(resource("bin")) + os.pathsep + os.environ.get("PATH", "")
