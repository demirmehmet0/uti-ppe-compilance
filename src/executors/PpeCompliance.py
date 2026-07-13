
import os
import sys
import time
import json

sys.path.append(os.path.join(os.path.dirname(__file__), '../../../../'))

from sdks.novavision.src.helper.executor import Executor
from sdks.novavision.src.base.component import Component
from sdks.novavision.src.base.logger import LoggerManager
from components.PpeCompliance.src.utils.response import build_response
from components.PpeCompliance.src.models.PackageModel import PackageModel

logger = LoggerManager()


class PpeCompliance(Component):
    """
    Per-track PPE compliance evaluator with single-record-per-track semantics.

    Consumes the per-frame `outputPersons` from cap-ppe-detection (PpeOnDetections),
    where every person carries a stable `trackerUUID`, a `requires` dict
    ({equipmentClass: bool} for THIS frame) and a per-frame `status` bool.

    For each tracked person it keeps a trailing time window (default 10 s) of the
    equipment classes seen at least once, and takes the UNION over that window:
      - if a required class was detected even once within the window, the person is
        credited with it (rule 1 & 2 - "1 kez bile tam ise tam", "yekune bakilir");
      - a violation is raised only when, after a full window of observation, some
        required class was NEVER seen in the window.

    A violation event is emitted at most ONCE per track lifetime (rule 3 - "track id
    gidene kadar baska kayit acilmaz"): the `recorded` flag stays set until the track
    disappears for longer than the grace period, at which point its state is dropped
    so a genuinely new entry can be recorded again.
    """

    def __init__(self, request, bootstrap):
        super().__init__(request, bootstrap)
        self.request.model = PackageModel(**(self.request.data))

        self.input_persons = self.request.get_param("inputPersons") or []
        self.window_seconds = float(self.request.get_param("configWindowSeconds") or 10.0)
        self.grace_period = float(self.request.get_param("configGracePeriod") or 0.0)
        self.required_override = self._parse_override(self.request.get_param("configRequiredOverride"))

        # Per-track state, cached across frames (same pattern as uti-time-in-zone).
        self.tracks = self.bootstrap.get("tracks", {})

    @staticmethod
    def bootstrap(config: dict) -> dict:
        return {"tracks": {}}

    def _parse_override(self, raw):
        """Decode the optional TextList override into a list of class names, or None."""
        if not raw:
            return None
        if isinstance(raw, list):
            return raw or None
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning(f"PpeCompliance - Could not parse configRequiredOverride: {raw!r}")
            return None
        return parsed if isinstance(parsed, list) and parsed else None

    @staticmethod
    def _track_key(person):
        """Stable identity for a tracked person across frames."""
        return person.get("trackerUUID") or person.get("trackerID") or person.get("person_id")

    def _seen_now(self, requires):
        """Equipment classes detected on this person THIS frame (requires[x] == True)."""
        return [cls for cls, present in requires.items() if present]

    def _required_set(self, requires):
        """Required classes: explicit override if given, else the keys PPE stamped."""
        if self.required_override:
            return list(self.required_override)
        return list(requires.keys())

    def _window_union(self, track, cutoff):
        """Union of every equipment class seen at least once within the window."""
        union = set()
        kept = []
        for ts, seen in track["window"]:
            if ts >= cutoff:
                kept.append((ts, seen))
                union.update(seen)
        track["window"] = kept
        return union

    def evaluate(self):
        now = time.time()
        violations = []
        seen_keys = set()

        for person in self.input_persons:
            key = self._track_key(person)
            if key is None:
                continue
            seen_keys.add(key)

            requires = person.get("requires") or {}
            required = self._required_set(requires)

            track = self.tracks.get(key)
            if track is None:
                track = {"window": [], "recorded": False, "first_seen": now}
                self.tracks[key] = track

            track["last_seen"] = now
            track["window"].append((now, self._seen_now(requires)))

            cutoff = now - self.window_seconds
            union = self._window_union(track, cutoff)

            missing = [cls for cls in required if cls not in union]
            warmed_up = (now - track["first_seen"]) >= self.window_seconds

            if required and missing and warmed_up and not track["recorded"]:
                event = dict(person)  # carry bbox / track ids / imgUID forward
                event["requires"] = {cls: (cls in union) for cls in required}
                event["status"] = False
                event["compliant"] = False
                event["missing"] = missing
                event["windowSeconds"] = self.window_seconds
                violations.append(event)
                track["recorded"] = True

        # Drop tracks absent longer than the grace period so a genuine re-entry
        # (new tracker id) can be evaluated - and recorded - afresh.
        stale = [
            key for key, track in self.tracks.items()
            if key not in seen_keys and (now - track.get("last_seen", now)) > self.grace_period
        ]
        for key in stale:
            del self.tracks[key]

        self.bootstrap["tracks"] = self.tracks
        return violations

    def run(self):
        self.violations = self.evaluate()
        packageModel = build_response(context=self)
        return packageModel


if "__main__" == __name__:
    Executor(sys.argv[1]).run()
