
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
    Per-track PPE aggregator with exactly-one-record-per-track semantics.

    Consumes the per-frame `outputPersons` from cap-ppe-detection (PpeOnDetections),
    where every person carries a stable `trackerUUID`/`trackerID`, a `requires` dict
    ({equipmentClass: bool} for THIS frame) and a per-frame `status` bool.

    Equipment sightings are accumulated per track as a UNION: a required class seen
    even a single time counts as worn ("1 kez bile gorulse var say"), so a momentarily
    occluded item does not raise a false violation. WHICH frames are accumulated and
    WHEN the record is emitted is chosen by `configEvaluationMode`:

      - "FirstWindow": counts only the opening `configWindowSeconds` of the track and
        emits the record the moment that window closes - the person is still in frame,
        so the alert arrives while they can be reached. Nothing more is emitted for
        that track, not even when it later leaves. A track that ends before its window
        filled still yields its single record, flagged `windowComplete = False`.
      - "FullTrack": counts every frame of the track with no pruning and emits the
        record when the track is lost (absent longer than the grace period).
        `configWindowSeconds` is unused in this mode.

    The record carries the aggregation so the decision is made downstream (Expression):
      - `requires`      : {class: bool} unioned over the aggregated frames
      - `detected`      : classes seen at least once
      - `missing`       : required classes never seen
      - `compliant`     : True when nothing required is missing (also mirrored to
                          `status`, overwriting the per-frame value PPE stamped)
      - `presence`      : {class: ratio} - fraction of counted frames each class was seen
      - `presenceRatio` : flat max presence over the required set (filter to drop stray
                          detections that only clipped into another person's ROI briefly)
      - `duration`      : seconds between the track's first and last sighting
      - `framesObserved`: number of frames actually counted
      - `evaluationMode`: which mode produced the record
      - `windowComplete`: False only when FirstWindow was cut short by an early exit
    e.g. phone-use -> filter `detected In "cell phone"` AND `presenceRatio > 0.3`;
         classic PPE -> `compliant == False`.
    """

    def __init__(self, request, bootstrap):
        super().__init__(request, bootstrap)
        self.request.model = PackageModel(**(self.request.data))

        self.input_persons = self.request.get_param("inputPersons") or []
        self.evaluation_mode = self.request.get_param("configEvaluationMode") or "FullTrack"
        self.first_window_mode = (self.evaluation_mode == "FirstWindow")
        self.window_seconds = float(self.request.get_param("configWindowSeconds") or 10.0)
        self.grace_period = float(self.request.get_param("configGracePeriod") or 2.0)
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

    def _finalize(self, track, now, window_complete=True):
        """Build the single summary record for a track from its accumulated counters.

        Sightings are kept as {class: frames_seen} plus a counted-frame total rather
        than a per-frame list, so a person standing in view for an hour costs the same
        memory as one walking past.

        `presence` is the per-class fraction of counted frames the class was seen in.
        This separates a person genuinely wearing/holding an item (high ratio) from one
        a stray detection only clipped into occasionally (low ratio) - filter downstream
        on `presenceRatio` to drop false attributions.
        """
        counts = track["counts"]
        total = track["total"] or 1
        union = {cls for cls, seen in counts.items() if seen}

        required = self.required_override or sorted(track.get("required_keys", set()))
        missing = [cls for cls in required if cls not in union]
        compliant = (not missing) if required else True

        # per-class fraction of counted frames the class was present
        presence = {cls: round(counts.get(cls, 0) / total, 3) for cls in required}
        if presence:
            presence_ratio = max(presence.values())          # required set (flat, no space-key)
        else:
            presence_ratio = round(max(counts.values(), default=0) / total, 3)  # else best seen

        event = dict(track.get("last_person") or {})  # carry bbox / track ids forward
        event["requires"] = {cls: (cls in union) for cls in required}
        event["detected"] = sorted(union)
        event["missing"] = sorted(missing)
        event["compliant"] = compliant
        # PPE stamped a PER-FRAME `status` on the person; at track level that value is
        # meaningless and contradicts `compliant`, so mirror the aggregated verdict.
        event["status"] = compliant
        event["presence"] = presence
        event["presenceRatio"] = presence_ratio
        # Weakest required item: `compliant` credits a class seen even once, which over a
        # long FullTrack barely means anything. presenceMin says how consistently the
        # WORST required item was actually worn, so a real PPE filter is
        # `compliant == False OR presenceMin < 0.8` rather than `compliant` alone.
        event["presenceMin"] = min(presence.values()) if presence else 0.0
        event["duration"] = round(track.get("last_seen", now) - track.get("first_seen", now), 2)
        event["framesObserved"] = track["total"]
        event["windowSeconds"] = self.window_seconds
        event["evaluationMode"] = self.evaluation_mode
        event["windowComplete"] = window_complete
        return event

    def _observe(self, key, person, now):
        """Fold this frame's sighting into the person's track.

        Returns the finalized record when FirstWindow's opening window closes on this
        frame (the person is still in view), otherwise None.
        """
        track = self.tracks.get(key)
        if track is None:
            track = {"counts": {}, "total": 0, "recorded": False,
                     "first_seen": now, "required_keys": set()}
            self.tracks[key] = track

        track["last_seen"] = now
        track["last_person"] = person  # carried into the final record

        # A track that already produced its record is kept alive (until it goes stale)
        # purely so the same person is not evaluated a second time.
        if track["recorded"]:
            return None

        requires = person.get("requires") or {}
        track["required_keys"].update(self._required_set(requires))

        elapsed = now - track["first_seen"]
        if not self.first_window_mode or elapsed <= self.window_seconds:
            track["total"] += 1
            for cls in self._seen_now(requires):
                track["counts"][cls] = track["counts"].get(cls, 0) + 1

        if self.first_window_mode and elapsed >= self.window_seconds:
            track["recorded"] = True
            return self._finalize(track, now)
        return None

    def _collect_stale(self, now, seen_keys):
        """Drop tracks absent longer than the grace period, recording those that have
        not been recorded yet.

        FullTrack builds its record here, over the whole track. FirstWindow normally
        recorded while the person was present; if they left before their window filled,
        the record is still emitted so no track goes unreported - flagged
        `windowComplete=False` for downstream filtering. Either way the state is dropped
        so a genuine re-entry (new tracker id) is evaluated afresh.
        """
        stale = [
            key for key, track in self.tracks.items()
            if key not in seen_keys and (now - track.get("last_seen", now)) > self.grace_period
        ]
        records = []
        for key in stale:
            track = self.tracks.pop(key)
            if not track["recorded"]:
                records.append(
                    self._finalize(track, now, window_complete=not self.first_window_mode)
                )
        return records

    def evaluate(self):
        now = time.time()
        seen_keys = set()
        records = []

        for person in self.input_persons:
            key = self._track_key(person)
            if key is None:
                continue
            seen_keys.add(key)
            record = self._observe(key, person, now)
            if record is not None:
                records.append(record)

        records.extend(self._collect_stale(now, seen_keys))

        self.bootstrap["tracks"] = self.tracks
        return records

    def run(self):
        self.violations = self.evaluate()
        packageModel = build_response(context=self)
        return packageModel


if "__main__" == __name__:
    Executor(sys.argv[1]).run()
