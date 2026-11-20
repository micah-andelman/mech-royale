"""Team "5-hour limit reached" Mech Royale bot. Logic lives in src/brain.py."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mechfight as mf  # noqa: E402
from src.brain import Brain  # noqa: E402

mf.run(Brain)
