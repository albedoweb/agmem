"""Go (.go) parser — package, types (struct/interface), functions, methods, HTTP routes.

Recognizes the gofmt-canonical syntax (which all real Go code follows):

- ``package <name>``
- ``type X struct {`` / ``type X interface {``
- ``func Name(...)`` — top-level function
- ``func (r *Receiver) Method(...)`` — method on a receiver
- chi / gin / echo / gorilla mux style HTTP route registrations:
  ``router.Get("/path", handler)``, ``mux.HandleFunc("/foo", handler)``,
  ``e.POST("/api", handler)`` — case-insensitive method match.

Generated files (``_mock.go``, ``.pb.go``, ``_gen.go``) and test files
(``_test.go``) are still parsed but tagged so consumers can filter.
"""

from __future__ import annotations

import re

from .types import Block

_GO_PACKAGE = re.compile(r"^\s*package\s+(\w+)")
_GO_TYPE_STRUCT = re.compile(r"^\s*type\s+(\w+)\s+struct\b")
_GO_TYPE_INTERFACE = re.compile(r"^\s*type\s+(\w+)\s+interface\b")
_GO_FUNC_METHOD = re.compile(r"^\s*func\s+\(\s*\w+\s+\*?(\w+)\s*\)\s+(\w+)\s*\(")
_GO_FUNC = re.compile(r"^\s*func\s+(\w+)\s*\(")
# Match `router.Get("/path",`, `router.POST("/x"`, `mux.HandleFunc("/y"`, etc.
# Method name is captured case-sensitively (so `Get`, `GET`, `Handle` all match).
_GO_ROUTE = re.compile(
    r"\.\s*(?P<method>Get|Post|Put|Patch|Delete|Head|Options|"
    r"GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|HandleFunc|Handle)"
    r"\s*\(\s*[\"`](?P<path>/[^\"`]*)[\"`]"
)


def analyze(content: str) -> list[Block]:
    blocks: list[Block] = []

    for line in content.splitlines():
        pkg = _GO_PACKAGE.match(line)
        if pkg:
            blocks.append(Block(block_type="package", name=pkg.group(1)))
            continue

        struct_match = _GO_TYPE_STRUCT.match(line)
        if struct_match:
            blocks.append(Block(block_type="struct", name=struct_match.group(1)))
            continue

        iface_match = _GO_TYPE_INTERFACE.match(line)
        if iface_match:
            blocks.append(Block(block_type="interface", name=iface_match.group(1)))
            continue

        # Method has priority over function — the receiver paren group
        # disambiguates from a plain ``func Name(``.
        method_match = _GO_FUNC_METHOD.match(line)
        if method_match:
            receiver_type = method_match.group(1)
            method_name = method_match.group(2)
            blocks.append(Block(
                block_type="method",
                name=method_name,
                labels=[receiver_type],
            ))
            continue

        func_match = _GO_FUNC.match(line)
        if func_match:
            blocks.append(Block(block_type="function", name=func_match.group(1)))
            continue

        # Route — search anywhere on the line (chained builders are common
        # in Go HTTP code).
        route_match = _GO_ROUTE.search(line)
        if route_match:
            method = route_match.group("method").upper()
            # Normalize HandleFunc/Handle to a method-agnostic "ANY" so the
            # tag set stays small and the block.labels stay meaningful.
            if method in ("HANDLEFUNC", "HANDLE"):
                method = "ANY"
            path = route_match.group("path")
            blocks.append(Block(
                block_type="route",
                name=path,
                labels=[method, path],
            ))

    return blocks


def summary(blocks: list[Block]) -> str:
    pkg = next((b.name for b in blocks if b.block_type == "package"), None)
    kind_counts: dict[str, int] = {}
    for b in blocks:
        if b.block_type == "package":
            continue
        kind_counts[b.block_type] = kind_counts.get(b.block_type, 0) + 1
    bits: list[str] = []
    for kind in ("route", "struct", "interface", "method", "function"):
        n = kind_counts.get(kind, 0)
        if n:
            bits.append(f"{n} {kind}{'s' if n != 1 else ''}")
    if pkg and bits:
        return f"Go file (package {pkg}) with {', '.join(bits)}"
    if pkg:
        return f"Go file (package {pkg})"
    if bits:
        return f"Go file with {', '.join(bits)}"
    return "Go file"


def extract_tags(path: str, blocks: list[Block]) -> list[str]:
    tags: set[str] = set()

    lower_path = path.lower()
    if lower_path.endswith("_test.go"):
        tags.add("test")
    if lower_path.endswith("_mock.go") or lower_path.endswith(".pb.go") or lower_path.endswith("_gen.go"):
        tags.add("generated")

    for b in blocks:
        if b.block_type == "package":
            tags.add(b.name)
            if b.name == "main":
                tags.add("binary")
                tags.add("entrypoint")
        elif b.block_type == "struct":
            tags.add("struct")
            _add_name_role_tags(b.name, tags)
        elif b.block_type == "interface":
            tags.add("interface")
            _add_name_role_tags(b.name, tags)
        elif b.block_type == "method":
            tags.add("method")
            # Receiver type can also carry a role hint.
            for lbl in b.labels:
                _add_name_role_tags(lbl, tags)
        elif b.block_type == "function":
            tags.add("function")
            if b.name.startswith("Test"):
                tags.add("test")
            elif b.name.startswith("Benchmark"):
                tags.add("benchmark")
            elif b.name == "main":
                tags.add("entrypoint")
        elif b.block_type == "route":
            tags.add("route")
            tags.add("api")
            if len(b.labels) == 2:
                method, route_path = b.labels
                tags.add(method.lower())
                for part in route_path.split("/"):
                    cleaned = part.strip("{}").strip().lower()
                    if cleaned and len(cleaned) > 1 and not (
                        cleaned.startswith("v") and cleaned[1:].isdigit()
                    ):
                        tags.add(cleaned)
                    elif cleaned.startswith("v") and cleaned[1:].isdigit():
                        tags.add(cleaned)

    return list(tags)


def _add_name_role_tags(name: str, tags: set[str]) -> None:
    """Conventional Go suffix-based role inference (Service, Repository, …).

    Lowercase the name once, then check suffixes. Cheap and surprisingly
    accurate on real Go codebases — the conventions are strong.
    """
    n = name.lower()
    if n.endswith("client"):
        tags.add("client")
    if n.endswith("server"):
        tags.add("server")
    if n.endswith("handler"):
        tags.add("handler")
    if n.endswith("service"):
        tags.add("service")
    if n.endswith("repository") or n.endswith("repo"):
        tags.add("repository")
    if n.endswith("config"):
        tags.add("config")
    if n.endswith("manager"):
        tags.add("manager")
    if n.endswith("middleware"):
        tags.add("middleware")
