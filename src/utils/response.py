
from components.PpeCompliance.src.models.PackageModel import (
    PackageModel, PackageConfigs, ConfigExecutor, PpeCompliance,
    PpeComplianceResponse, PpeComplianceOutputs, OutputViolations,
)
from sdks.novavision.src.helper.package import PackageHelper


def build_response(context):
    outputViolations = OutputViolations(value=context.violations)
    ppeComplianceOutputs = PpeComplianceOutputs(outputViolations=outputViolations)
    ppeComplianceResponse = PpeComplianceResponse(outputs=ppeComplianceOutputs)
    ppeComplianceExecutor = PpeCompliance(value=ppeComplianceResponse)
    executor = ConfigExecutor(value=ppeComplianceExecutor)
    packageConfigs = PackageConfigs(executor=executor)
    package = PackageHelper(packageModel=PackageModel, packageConfigs=packageConfigs)
    packageModel = package.build_model(context)
    return packageModel
