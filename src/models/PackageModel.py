
from pydantic import Field
from typing import List, Optional, Union, Literal
from sdks.novavision.src.base.model import (
    Inputs, Detection, Input, Package, Output, Config, Configs, Outputs, Response, Request,
)


class InputPersons(Input):
    """
    Person detections coming from cap-ppe-detection (PpeOnDetections) `outputPersons`.
    Each item is a per-frame person carrying a stable `trackerUUID` (from
    cap-object-tracking), a `requires` dict ({equipmentClass: bool} - which required
    PPE classes were present on that person THIS frame) and a per-frame `status` bool.
    These extra fields survive because the SDK base Model allows extra attributes.
    """
    name: Literal["inputPersons"] = "inputPersons"
    value: List[Detection]
    type: Literal["list"] = "list"

    class Config:
        title = "PpePersons"


class OutputViolations(Output):
    """
    Per-track summary events. Emitted exactly ONCE per track lifetime, for EVERY track
    - not only violations. When the record appears depends on the evaluation mode:
    "First Window" emits it as soon as the opening window closes (person still in
    frame), "Full Track" emits it when the track is lost. Each event carries the track
    ids plus the aggregation: `requires` ({class: bool} union over the aggregated
    frames), `detected`, `missing`, `compliant` (bool), `presence`/`presenceRatio`,
    `duration` (s), `framesObserved`, `evaluationMode` and `windowComplete`. The flow
    decides downstream via Expression (e.g. phone-use -> `detected In "cell phone"`
    with `presenceRatio > 0.3`; classic PPE -> `compliant == False`).
    """
    name: Literal["outputViolations"] = "outputViolations"
    value: List[Detection]
    type: Literal["list"] = "list"

    class Config:
        title = "Violations"


class ConfigWindowSeconds(Config):
    """
    Length of the observation window (in seconds) used to decide compliance PER
    TRACK. Across this window the system takes the UNION of every equipment class
    seen at least once: if a required class was detected even a single time within
    the window, that person is credited with it, so a momentarily occluded item
    does not cause a false violation.

    ONLY USED BY THE "First Window" EVALUATION MODE, where it is the length of the
    opening window measured from the moment the person is first tracked, and also
    the moment the single record is emitted. In "Full Track" mode the whole track
    lifetime is the window and this value is ignored.
    """
    name: Literal["configWindowSeconds"] = "configWindowSeconds"
    value: float = Field(ge=0.1, le=3600.0, default=10.0)
    type: Literal["number"] = "number"
    field: Literal["textInput"] = "textInput"
    placeHolder: Literal["[0.1, 3600.0]"] = "[0.1, 3600.0]"

    class Config:
        title = "Window Seconds"
        json_schema_extra = {
            "shortDescription": "Opening window (s) for First Window mode; ignored by Full Track."
        }


class ConfigGracePeriod(Config):
    """
    How many seconds a track may disappear before its accumulated state is cleared.
    Brief single-frame gaps (occlusion, a momentary confidence dip) should not be
    treated as the person leaving; the grace period keeps the track's window and its
    'already recorded' flag alive across such gaps. Once a track has been absent
    longer than this, its state is dropped, so if the SAME person re-enters (as a new
    tracker id) a fresh evaluation - and a fresh single record - can occur.
    """
    name: Literal["configGracePeriod"] = "configGracePeriod"
    value: float = Field(ge=0.0, le=60.0, default=2.0)
    type: Literal["number"] = "number"
    field: Literal["textInput"] = "textInput"
    placeHolder: Literal["[0.0, 60.0]"] = "[0.0, 60.0]"

    class Config:
        title = "Grace Period"
        json_schema_extra = {
            "shortDescription": "Occlusion tolerance in seconds before a track's state resets (0 = disable)."
        }


class ConfigRequiredOverride(Config):
    """
    OPTIONAL. Leave empty to derive the required equipment set automatically from the
    keys of each person's `requires` dict (i.e. exactly the required classes you set
    on the cap-ppe-detection node). Provide a JSON list of class names here ONLY if
    you want this node to enforce a different/narrower required set than PPE reported.
    Renders a TextList widget; the value is a JSON-encoded list of strings.
    """
    name: Literal["configRequiredOverride"] = "configRequiredOverride"
    value: Optional[str] = ""
    type: Literal["string"] = "string"
    field: Literal["widget"] = "widget"

    class Config:
        title = "Required Override"
        json_schema_extra = {
            "class": "\\novavision\\app\\widgets\\TextList",
            "shortDescription": "Optional: override the required PPE classes (else taken from PPE)."
        }


class EvaluationFirstWindow(Config):
    """
    Judge each person over the OPENING window of their track: counting starts the
    moment the person is first tracked and stops after Window Seconds, at which
    point the single record for that track is emitted immediately - the person is
    still in frame. Nothing further is emitted for that track, not even when it
    later leaves. Use this when an alert has to arrive while the person can still
    be reached (entrance PPE checks).
    """
    name: Literal["FirstWindow"] = "FirstWindow"
    value: Literal["FirstWindow"] = "FirstWindow"
    type: Literal["string"] = "string"
    field: Literal["option"] = "option"

    class Config:
        title = "First Window"


class EvaluationFullTrack(Config):
    """
    Judge each person over their ENTIRE track: every frame the person is detected
    contributes, with no window pruning, and the single record is emitted when the
    track is lost (absent longer than the Grace Period). Window Seconds is ignored.
    Use this when the complete visit should be summarised (reporting, dwell-based
    analysis) and a delayed record is acceptable.
    """
    name: Literal["FullTrack"] = "FullTrack"
    value: Literal["FullTrack"] = "FullTrack"
    type: Literal["string"] = "string"
    field: Literal["option"] = "option"

    class Config:
        title = "Full Track"


class ConfigEvaluationMode(Config):
    """
    Chooses WHICH frames of a track are aggregated and WHEN the single per-track
    record is emitted. Either way exactly one record is produced per track.
    """
    name: Literal["configEvaluationMode"] = "configEvaluationMode"
    value: Union[EvaluationFullTrack, EvaluationFirstWindow]
    type: Literal["object"] = "object"
    field: Literal["dropdownlist"] = "dropdownlist"

    class Config:
        title = "Evaluation Mode"
        json_schema_extra = {
            "shortDescription": "First Window (emit early, while present) or Full Track (emit at track loss)."
        }


class PpeComplianceConfigs(Configs):
    configEvaluationMode: ConfigEvaluationMode
    configWindowSeconds: ConfigWindowSeconds
    configGracePeriod: ConfigGracePeriod
    configRequiredOverride: ConfigRequiredOverride


class PpeComplianceInputs(Inputs):
    inputPersons: InputPersons


class PpeComplianceOutputs(Outputs):
    outputViolations: OutputViolations


class PpeComplianceResponse(Response):
    outputs: PpeComplianceOutputs


class PpeComplianceRequest(Request):
    inputs: PpeComplianceInputs
    configs: PpeComplianceConfigs

    class Config:
        json_schema_extra = {
            "target": "configs"
        }


class PpeCompliance(Config):
    name: Literal["PpeCompliance"] = "PpeCompliance"
    value: Union[PpeComplianceRequest, PpeComplianceResponse]
    type: Literal["object"] = "object"
    field: Literal["option"] = "option"

    class Config:
        title = "PPE Compliance"
        json_schema_extra = {
            "target": {
                "value": 0
            }
        }


class ConfigExecutor(Config):
    name: Literal["ConfigExecutor"] = "ConfigExecutor"
    value: Union[PpeCompliance]
    type: Literal["executor"] = "executor"
    field: Literal["dependentDropdownlist"] = "dependentDropdownlist"
    restart: Literal[True] = True

    class Config:
        title = "Executor"
        json_schema_extra = {
            "target": "value"
        }


class PackageConfigs(Configs):
    executor: ConfigExecutor


class PackageModel(Package):
    configs: PackageConfigs
    type: Literal["component"] = "component"
    name: Literal["PpeCompliance"] = "PpeCompliance"
