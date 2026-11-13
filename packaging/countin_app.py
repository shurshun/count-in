import multiprocessing
import os
import sys

from countin.gui import main

if __name__ == "__main__":
    # A windowed build on Windows has no console; anything printed after this goes nowhere. Errors go to the log.
    if sys.stderr is None:
        sys.stdout = sys.stderr = open(os.devnull, "w")
    multiprocessing.freeze_support()
    sys.exit(main())
