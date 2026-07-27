
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
    Per-track PPE aggregator with one-record-per-track-at-track-loss semantics.

    Consumes the per-frame `outputPersons` from cap-ppe-detection (PpeOnDetections),
    where every person carries a stable `trackerUUID`/`trackerID`, a `requires` dict
    ({equipmentClass: bool} for THIS frame) and a per-frame `status` bool.

    For each tracked person it keeps a trailing time window (default 10 s) and takes
    the UNION of equipment classes seen at least once within it, so a momentarily
    occluded item still counts ("1 kez bile gorulse var say").

    A single summary record is emitted per track WHEN THE TRACK LEAVES (absent for
    longer than the grace period) - for EVERY track, not only violations. The record
    carries the windowed union so the decision is made downstream (Expression):
      - `requires`     : {class: bool} unioned over the window
      - `detected`     : classes seen at least once in the window
      - `missing`      : required classes never seen in the window
      - `compliant`    : True when nothing required is missing
      - `presence`     : {class: ratio} - fraction of observed frames each class was seen
      - `presenceRatio`: flat max presence over the required set (filter to drop stray
                         detections that only clipped into another person's ROI briefly)
      - `duration`     : seconds the track was observed
      - `framesObserved`: number of frames the track was detected in the window
    e.g. phone-use -> filter `detected In "cell phone"` AND `presenceRatio > 0.3`;
         classic PPE -> `compliant == False`.
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

    def _finalize(self, track, now):
        """Build the single summary record emitted when a track leaves. Unions over
        the sightings still retained (pruned at last presence, NOT re-pruned now, so
        waiting out the grace period never empties the union).

        Also computes a per-class PRESENCE ratio = fraction of the track's observed
        frames in which the class was seen. This separates a person genuinely holding
        an item (high ratio) from one a stray detection only clipped into occasionally
        (low ratio) - filter downstream on `presenceRatio` to drop false attributions.
        """
        union = set()
        counts = {}
        for _ts, seen in track["window"]:
            union.update(seen)
            for cls in seen:
                counts[cls] = counts.get(cls, 0) + 1
        total = len(track["window"]) or 1

        required = self.required_override or sorted(track.get("required_keys", set()))
        missing = [cls for cls in required if cls not in union]

        # per-class fraction of observed frames the class was present
        presence = {cls: round(counts.get(cls, 0) / total, 3) for cls in required}
        if presence:
            presence_ratio = max(presence.values())          # required set (flat, no space-key)
        else:
            presence_ratio = round(max(counts.values(), default=0) / total, 3)  # else best seen

        event = dict(track.get("last_person") or {})  # carry bbox / track ids forward
        event["requires"] = {cls: (cls in union) for cls in required}
        event["detected"] = sorted(union)
        event["missing"] = sorted(missing)
        event["compliant"] = (not missing) if required else True
        event["presence"] = presence
        event["presenceRatio"] = presence_ratio
        event["duration"] = round(track.get("last_seen", now) - track.get("first_seen", now), 2)
        event["framesObserved"] = total
        event["windowSeconds"] = self.window_seconds
        return event

    def evaluate(self):
        now = time.time()
        seen_keys = set()

        # 1) Update per-track window state for everyone seen THIS frame.
        #    Nothing is emitted while a track is still present.
        for person in self.input_persons:
            key = self._track_key(person)
            if key is None:
                continue
            seen_keys.add(key)

            requires = person.get("requires") or {}

            track = self.tracks.get(key)
            if track is None:
                track = {"window": [], "recorded": False,
                         "first_seen": now, "required_keys": set()}
                self.tracks[key] = track

            track["last_seen"] = now
            track["last_person"] = person  # carried into the final record
            track["required_keys"].update(self._required_set(requires))
            track["window"].append((now, self._seen_now(requires)))
            # keep only sightings inside the trailing window (pruned while present)
            cutoff = now - self.window_seconds
            track["window"] = [(ts, seen) for ts, seen in track["window"] if ts >= cutoff]

        # 2) A track absent longer than the grace period is 'gone': emit ONE
        #    summary record for it (every track, not only violations), then drop it
        #    so a genuine re-entry (new tracker id) is evaluated afresh.
        records = []
        stale = [
            key for key, track in self.tracks.items()
            if key not in seen_keys and (now - track.get("last_seen", now)) > self.grace_period
        ]
        for key in stale:
            track = self.tracks[key]
            if not track.get("recorded"):
                records.append(self._finalize(track, now))
            del self.tracks[key]

        self.bootstrap["tracks"] = self.tracks
        return records

    def run(self):
        self.violations = self.evaluate()
        packageModel = build_response(context=self)
        return packageModel


if "__main__" == __name__:
    Executor(sys.argv[1]).run()
