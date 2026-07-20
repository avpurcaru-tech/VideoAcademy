"""Provider-neutral song boundary.

Future adapters may map EducationalSongBrief -> LyricsPlan and brief + lyrics ->
an audio artifact. This package only combines LyricsPlan + MusicPlan into a
SongProductionPlan for later Episode/Director integration; it performs no AI or
media generation.
"""

from .contracts import *
from .planner import *
from .lyrics_generator import *
from .lyrics_service import *
from .lyrics_writer import *
