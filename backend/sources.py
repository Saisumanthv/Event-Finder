import sources_courses
import sources_events
import sources_hackathons
from sources_core import gfg_events, unstop_hackathons

SOURCES = {
    "event_details": {"GeeksforGeeks": gfg_events, **sources_events.SOURCES},
    "hackathon_details": {"Unstop": unstop_hackathons, **sources_hackathons.SOURCES},
    "new_courses": {**sources_courses.SOURCES},
}
