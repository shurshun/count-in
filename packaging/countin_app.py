import multiprocessing
import os
import sys

from countin.gui import main

if __name__ == "__main__":
    # A windowed build on Windows has no console; libraries that print warnings need somewhere to write.
    if sys.stderr is None:
        sys.stdout = sys.stderr = open(os.devnull, "w")
    multiprocessing.freeze_support()
    sys.exit(main())
