
from pydantic import Field
from typing import List, Optional, Union, Literal
from sdks.novavision.src.base.model import (
    Inputs, Detection, Image, Input, Package, Output, Config, Configs, Outputs, Response, Request,
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


class InputImage(Input):
    """
    OPTIONAL. The frame the persons were detected in. Connect it only if you want the
    node to hand back the snapshot of the moment a track was LAST SEEN (see
    `outputImage`). The aggregation itself does not need it.
    """
    name: Literal["inputImage"] = "inputImage"
    value: Union[List[Image], Image]
    type: str = "object"

    class Config:
        title = "Image"


class OutputImage(Output):
    """
    The frame in which the emitted track was LAST SEEN - not the live frame.

    Records are emitted once the track has already left the scene, so the frame that
    is current at emission time no longer contains the person. While a track is alive
    this node keeps the last frame it was seen in, and republishes that frame here
    together with the record, so a downstream File Save / Notification attaches a
    snapshot that actually shows the person.

    On frames where nothing is emitted the input frame is passed through untouched.
    If several tracks leave on the same frame, the snapshot belongs to the first
    record in `outputViolations`. Requires `inputImage` to be connected; stays empty
    otherwise.
    """
    name: Literal["outputImage"] = "outputImage"
    value: Optional[Union[List[Image], Image]] = None
    type: str = "object"

    class Config:
        title = "Snapshot"


class OutputViolations(Output):
    """
    Per-track summary events. Emitted at most ONCE per track lifetime, WHEN THE TRACK
    LEAVES the scene (absent longer than the grace period), for EVERY track - not only
    violations. Each event carries the track ids plus the windowed aggregation:
    `requires` ({class: bool} union over the window), `detected`, `missing`,
    `compliant` (bool) and `duration` (s). The flow decides downstream via Expression
    (e.g. phone-use -> `requires.phone == True`; classic PPE -> `compliant == False`).
    """
    name: Literal["outputViolations"] = "outputViolations"
    value: List[Detection]
    type: Literal["list"] = "list"

    class Config:
        title = "Violations"


class ConfigWindowSeconds(Config):
    """
    Length of the trailing time window (in seconds) used to decide compliance PER
    TRACK. Across this window the system takes the UNION of every equipment class
    seen at least once: if a required class was detected even a single time within
    the window, that person is credited with it. Compliance is only evaluated after
    the track has been observed for at least this many seconds (warm-up), so a
    momentarily occluded item does not cause a premature violation.
    """
    name: Literal["configWindowSeconds"] = "configWindowSeconds"
    value: float = Field(ge=0.1, le=3600.0, default=10.0)
    type: Literal["number"] = "number"
    field: Literal["textInput"] = "textInput"
    placeHolder: Literal["[0.1, 3600.0]"] = "[0.1, 3600.0]"

    class Config:
        title = "Window Seconds"
        json_schema_extra = {
            "shortDescription": "Trailing window (s) over which equipment sightings are unioned per track."
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


class PpeComplianceConfigs(Configs):
    configWindowSeconds: ConfigWindowSeconds
    configGracePeriod: ConfigGracePeriod
    configRequiredOverride: ConfigRequiredOverride


class PpeComplianceInputs(Inputs):
    inputPersons: InputPersons
    inputImage: Optional[InputImage] = None


class PpeComplianceOutputs(Outputs):
    outputViolations: OutputViolations
    outputImage: Optional[OutputImage] = None


class PpeComplianceResponse(Response):
    outputs: PpeComplianceOutputs


class PpeComplianceRequest(Request):
    inputs: Optional[PpeComplianceInputs]
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
