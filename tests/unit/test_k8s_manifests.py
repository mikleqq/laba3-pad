"""Static checks of the Kubernetes manifests (run in CI before anything is deployed)."""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
K8S = ROOT / "k8s"
SERVICES = ["auth-service", "user-service", "product-service", "order-service", "payment-service", "notification-service"]


def load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def all_manifests() -> list[dict]:
    kustomization = load(K8S / "kustomization.yaml")
    return [load(K8S / rel) for rel in kustomization["resources"]]


def workloads() -> list[dict]:
    return [m for m in all_manifests() if m["kind"] in ("Deployment", "StatefulSet")]


def test_every_listed_file_exists_and_is_in_the_namespace():
    for manifest in all_manifests():
        if manifest["kind"] != "Namespace":
            assert manifest["metadata"]["namespace"] == "shophub", manifest["metadata"]["name"]


def test_every_backend_service_has_a_deployment_and_a_service():
    kinds = {(m["kind"], m["metadata"]["name"]) for m in all_manifests()}
    for name in SERVICES + ["gateway"]:
        assert ("Deployment", name) in kinds
        assert ("Service", name) in kinds


@pytest.mark.parametrize("workload", workloads(), ids=lambda m: m["metadata"]["name"])
def test_selector_matches_pod_labels(workload):
    selector = workload["spec"]["selector"]["matchLabels"]
    labels = workload["spec"]["template"]["metadata"]["labels"]
    assert selector.items() <= labels.items()


@pytest.mark.parametrize("workload", workloads(), ids=lambda m: m["metadata"]["name"])
def test_containers_have_resources_and_probes(workload):
    for container in workload["spec"]["template"]["spec"]["containers"]:
        assert {"requests", "limits"} <= container["resources"].keys()
        assert "livenessProbe" in container and "readinessProbe" in container


@pytest.mark.parametrize("name", SERVICES)
def test_services_use_health_and_ready_endpoints(name):
    container = load(K8S / "deployments" / f"{name}.yaml")["spec"]["template"]["spec"]["containers"][0]
    assert container["livenessProbe"]["httpGet"]["path"] == "/health"
    assert container["readinessProbe"]["httpGet"]["path"] == "/ready"
    assert container["securityContext"]["allowPrivilegeEscalation"] is False


@pytest.mark.parametrize("name", SERVICES)
def test_service_selects_the_deployment_pods(name):
    service = load(K8S / "services" / f"{name}.yaml")
    deployment = load(K8S / "deployments" / f"{name}.yaml")
    assert service["spec"]["selector"].items() <= deployment["spec"]["template"]["metadata"]["labels"].items()


def test_no_secret_values_in_manifests():
    for manifest in all_manifests():
        assert manifest["kind"] != "Secret", "secrets must not be committed"
        for workload_env in _env_vars(manifest):
            name = workload_env["name"]
            if any(word in name for word in ("PASSWORD", "SECRET", "TOKEN")):
                assert "valueFrom" in workload_env and "secretKeyRef" in workload_env["valueFrom"], name


def _env_vars(manifest):
    if manifest["kind"] not in ("Deployment", "StatefulSet"):
        return []
    spec = manifest["spec"]["template"]["spec"]
    containers = spec.get("initContainers", []) + spec["containers"]
    return [e for c in containers for e in c.get("env", [])]


def test_postgres_init_configmap_matches_the_compose_script():
    configmap = load(K8S / "configmaps" / "postgres-init.yaml")
    script = (ROOT / "database" / "init" / "01-shophub.sh").read_text(encoding="utf-8")
    assert configmap["data"]["01-shophub.sh"].strip() == script.strip()
