# uti-ppe-compliance

Per-track PPE (personal protective equipment) compliance evaluator with
single-record-per-track semantics.

Consumes the per-frame `outputPersons` of `cap-ppe-detection` (**PpeOnDetections**) —
each person carries a stable `trackerUUID` (from `cap-object-tracking`), a `requires`
dict (`{equipmentClass: bool}` for the current frame) and a per-frame `status` bool.

## Behaviour

- Keeps, per `trackerUUID`, a trailing time window (default **10 s**) of the equipment
  classes seen at least once, and takes the **union** over that window. If a required
  class was detected even a single time inside the window, the person is credited with
  it — so a momentarily occluded item does not raise a false alarm.
- A violation is raised only after the track has been observed for a full window
  (warm-up) and some required class was **never** seen within the window.
- A violation event is emitted **at most once per track lifetime**. The `recorded`
  flag persists until the track disappears for longer than the grace period, at which
  point the track's state is dropped so a genuine re-entry (a new tracker id) can be
  recorded again.

## Executor

`PpeCompliance` — input `inputPersons` (List[Detection]), output `outputViolations`
(List[Detection], usually 0 or 1 items per frame).

## Configs

| name | default | meaning |
|------|---------|---------|
| `configWindowSeconds` | 10.0 | trailing window over which equipment sightings are unioned per track |
| `configGracePeriod` | 2.0 | seconds a track may vanish before its state resets |
| `configRequiredOverride` | *(empty)* | optional JSON list to override the required classes (else taken from PPE's `requires` keys) |
