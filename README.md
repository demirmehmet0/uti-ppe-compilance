# uti-ppe-compliance

Per-track PPE (personal protective equipment) compliance evaluator with
exactly-one-record-per-track semantics.

Consumes the per-frame `outputPersons` of `cap-ppe-detection` (**PpeOnDetections**) —
each person carries a stable `trackerUUID` (from `cap-object-tracking`), a `requires`
dict (`{equipmentClass: bool}` for the current frame) and a per-frame `status` bool.

## Behaviour

Equipment sightings are accumulated per `trackerUUID` as a **union**: a required class
seen even a single time counts as worn, so a momentarily occluded item does not raise a
false violation. Exactly **one** record is produced per track, for every track — not
only violations. `configEvaluationMode` picks which frames are counted and when the
record is emitted:

| mode | frames counted | record emitted |
|------|----------------|----------------|
| **First Window** | the opening `configWindowSeconds` of the track | the moment that window closes — **the person is still in frame** |
| **Full Track** | every frame of the track, no pruning | when the track is lost (absent longer than `configGracePeriod`) |

- **First Window** is for alerts that must arrive while the person can still be reached
  (entrance PPE checks). Nothing further is emitted for that track, not even when it
  later leaves; a track that ends before its window filled still yields its single
  record, flagged `windowComplete = false`.
- **Full Track** summarises the complete visit (reporting, dwell analysis) at the cost
  of a delayed record. A person who never leaves the frame produces **no** record.
- Grace period: brief gaps (occlusion, a confidence dip) do not end a track. Once a
  track has been absent longer than the grace period its state is dropped, so a genuine
  re-entry (a new tracker id) is evaluated afresh — and produces its own record.

## Executor

`PpeCompliance` — input `inputPersons` (List[Detection]), output `outputViolations`
(List[Detection], usually 0 or 1 items per frame).

Each record carries the last frame's person fields (bbox, `confidence`, `trackerUUID`, …)
plus the aggregation:

| field | meaning |
|-------|---------|
| `requires` | `{class: bool}` union over the counted frames |
| `detected` | classes seen at least once |
| `missing` | required classes never seen |
| `compliant` | `true` when nothing required is missing (also mirrored to `status`) |
| `presence` | `{class: ratio}` — fraction of counted frames each required class was seen in |
| `presenceRatio` | **max** presence over the required set — drops stray detections that only clipped into another person's box (phone-use) |
| `presenceMin` | **min** presence over the required set — how consistently the *weakest* required item was worn (classic PPE) |
| `duration` | seconds between the track's first and last sighting |
| `framesObserved` | number of frames actually counted |
| `windowSeconds` | echo of the config |
| `evaluationMode` | `FirstWindow` / `FullTrack` |
| `windowComplete` | `false` only when First Window was cut short by an early exit |

Downstream filtering (Expression → If):

- classic PPE, First Window: `compliant == False`
- classic PPE, Full Track: `compliant == False` **or** `presenceMin < 0.8` — over a long
  track `compliant` credits an item seen in a single frame, so it alone is too weak
- phone-use: `detected In "cell phone"` **and** `presenceRatio > 0.3`
- drop low-evidence records: `windowComplete == True`, or `duration > 2`

## Configs

| name | default | meaning |
|------|---------|---------|
| `configEvaluationMode` | `FullTrack` | `FirstWindow` (emit early, while present) or `FullTrack` (emit at track loss) |
| `configWindowSeconds` | 10.0 | length of the opening window in First Window mode; **ignored** by Full Track |
| `configGracePeriod` | 2.0 | seconds a track may vanish before its state resets |
| `configRequiredOverride` | *(empty)* | optional JSON list to override the required classes (else taken from PPE's `requires` keys) |
