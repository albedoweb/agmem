from __future__ import annotations

from agmem.parsers import analyze_file, registered_extensions
from agmem.parsers.yaml import analyze, extract_header, extract_tags, summary

HELM_VALUES = """\
# Default values for storefront.
# Declare variables to be passed into your templates.
replicaCount: 2
image:
  repository: ghcr.io/northwind/storefront
  tag: "1.4.2"
storefront:
  env:
    FEATURE_INSIGHTS: "true"
    LOG_LEVEL: info
resources:
  limits:
    memory: 512Mi
args:
  - --serve
  - --port=8080
"""

K8S_MULTIDOC = """\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: api
spec:
  template:
    spec:
      containers:
        - name: web
          image: nginx:1.27
        - name: sidecar
          image: envoy:1.31
---
apiVersion: v1
kind: Service
metadata:
  name: api-svc
"""


class TestAnalyze:
    def test_flattens_nested_to_dotted_paths(self):
        names = {b.name for b in analyze(HELM_VALUES)}
        assert "image.repository" in names
        assert "storefront.env.FEATURE_INSIGHTS" in names
        assert "resources.limits.memory" in names

    def test_scalar_value_kept_as_label(self):
        blocks = {b.name: b for b in analyze(HELM_VALUES)}
        assert blocks["storefront.env.FEATURE_INSIGHTS"].labels == ["true"]
        assert blocks["image.repository"].labels == ["ghcr.io/northwind/storefront"]

    def test_full_name_carries_path_and_value(self):
        # The index body is built from block.full_name, so the dotted path and
        # value must both appear there for BM25 to match.
        fn = {b.name: b.full_name for b in analyze(HELM_VALUES)}
        assert fn["storefront.env.FEATURE_INSIGHTS"] == "key storefront.env.FEATURE_INSIGHTS (true)"

    def test_list_of_scalars_joined(self):
        blocks = {b.name: b for b in analyze(HELM_VALUES)}
        assert "args" in blocks
        assert blocks["args"].labels == ["--serve, --port=8080"]

    def test_list_of_mappings_collapsed(self):
        names = {b.name for b in analyze(K8S_MULTIDOC)}
        # numeric indices dropped, parent key is the path
        assert "spec.template.spec.containers.name" in names
        assert "spec.template.spec.containers.image" in names
        assert "spec.template.spec.containers.0.image" not in names

    def test_multidoc_walked(self):
        names = {b.name for b in analyze(K8S_MULTIDOC)}
        assert "kind" in names
        assert "metadata.name" in names

    def test_empty_and_invalid(self):
        assert analyze("") == []
        assert analyze("   \n  ") == []
        assert analyze("key: [unclosed") == []

    def test_bool_and_none_handling(self):
        blocks = {b.name: b for b in analyze("a: true\nb: false\nc: null\nd: 3")}
        assert blocks["a"].labels == ["true"]
        assert blocks["b"].labels == ["false"]
        assert blocks["c"].labels == []   # null → no label
        assert blocks["d"].labels == ["3"]


class TestSummary:
    def test_mentions_count_and_top_level(self):
        s = summary(analyze(HELM_VALUES))
        assert s.startswith("YAML file — ")
        assert "top-level:" in s
        assert "image" in s and "storefront" in s

    def test_empty(self):
        assert summary([]) == "YAML file"


class TestExtractHeader:
    def test_leading_comment_block(self):
        h = extract_header(HELM_VALUES)
        assert h.startswith("Default values for storefront.")

    def test_stops_at_first_key(self):
        h = extract_header("# one\nkey: v\n# two")
        assert h == "one"

    def test_no_header(self):
        assert extract_header("key: value") == ""

    def test_capped_at_200(self):
        assert len(extract_header("# " + "x " * 300)) <= 200


class TestExtractTags:
    def test_helm_values(self):
        tags = set(extract_tags("charts/storefront/values.yaml", analyze(HELM_VALUES)))
        assert "yaml" in tags
        assert "helm" in tags
        assert "values" in tags
        assert "image" in tags  # top-level key

    def test_k8s_kind(self):
        tags = set(extract_tags("k8s/api.yaml", analyze(K8S_MULTIDOC)))
        assert "k8s" in tags
        assert "deployment" in tags
        assert "service" in tags

    def test_github_actions(self):
        tags = set(extract_tags(".github/workflows/ci.yaml", analyze("on: push\njobs: {}")))
        assert "ci" in tags
        assert "github-actions" in tags


class TestRegistration:
    def test_yaml_yml_values_registered(self):
        exts = registered_extensions()
        assert "yaml" in exts
        assert "yml" in exts
        assert "values" in exts  # Helm *.values override files

    def test_analyze_file_dispatches_helm_values(self):
        # Helm-style bare `.values` override file (YAML content, no .yaml suffix)
        fa = analyze_file("apps/helm/northwind-mcp-server/dev.values",
                          "namespace: platform-dev\nname: northwind-mcp\nenv: dev\n")
        assert fa is not None
        assert fa.ext == "values"
        assert any(b.name == "namespace" for b in fa.blocks)

    def test_analyze_file_dispatches_yaml(self):
        fa = analyze_file("charts/x/values.yaml", HELM_VALUES)
        assert fa is not None
        assert fa.ext == "yaml"
        assert fa.header_comment.startswith("Default values")
        assert any(b.name == "storefront.env.FEATURE_INSIGHTS" for b in fa.blocks)

    def test_analyze_file_dispatches_yml(self):
        fa = analyze_file("config.yml", "a:\n  b: 1")
        assert fa is not None
        assert fa.ext == "yml"
        assert any(b.name == "a.b" for b in fa.blocks)
