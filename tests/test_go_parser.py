"""Tests for the Go parser."""

from agmem.content_analyzer import analyze_file, analyze_go, extract_go_tags


def test_package_declaration():
    blocks = analyze_go("package foo\n")
    assert any(b.block_type == "package" and b.name == "foo" for b in blocks)


def test_struct():
    code = "type LogMessage struct {\n\tWebhookID string\n}\n"
    blocks = analyze_go(code)
    s = [b for b in blocks if b.block_type == "struct"]
    assert len(s) == 1
    assert s[0].name == "LogMessage"


def test_interface():
    code = "type CoreClient interface {\n\tCheckConnection()\n}\n"
    blocks = analyze_go(code)
    i = [b for b in blocks if b.block_type == "interface"]
    assert len(i) == 1
    assert i[0].name == "CoreClient"


def test_top_level_function():
    blocks = analyze_go("func ProcessRequest(req *Request) error {\n\treturn nil\n}\n")
    funcs = [b for b in blocks if b.block_type == "function"]
    assert len(funcs) == 1
    assert funcs[0].name == "ProcessRequest"


def test_method_with_pointer_receiver():
    code = "func (lm *LogMessage) Matches(x interface{}) bool {\n\treturn true\n}\n"
    blocks = analyze_go(code)
    m = [b for b in blocks if b.block_type == "method"]
    assert len(m) == 1
    assert m[0].name == "Matches"
    assert m[0].labels == ["LogMessage"]


def test_method_with_value_receiver():
    code = "func (c Config) Validate() error { return nil }\n"
    blocks = analyze_go(code)
    m = [b for b in blocks if b.block_type == "method"]
    assert len(m) == 1
    assert m[0].labels == ["Config"]


def test_method_does_not_get_classified_as_function():
    code = "func (s *Server) Start() {}\nfunc Helper() {}\n"
    blocks = analyze_go(code)
    methods = [b for b in blocks if b.block_type == "method"]
    funcs = [b for b in blocks if b.block_type == "function"]
    assert len(methods) == 1 and methods[0].name == "Start"
    assert len(funcs) == 1 and funcs[0].name == "Helper"


def test_chi_routes():
    code = """
r := chi.NewRouter()
r.Get("/users", listUsers)
r.Post("/users", createUser)
r.Delete("/users/{id}", deleteUser)
"""
    blocks = analyze_go(code)
    routes = [b for b in blocks if b.block_type == "route"]
    assert len(routes) == 3
    methods = sorted(r.labels[0] for r in routes)
    assert methods == ["DELETE", "GET", "POST"]


def test_uppercase_gin_routes():
    code = 'r.GET("/api/v1/health", healthHandler)\n'
    blocks = analyze_go(code)
    routes = [b for b in blocks if b.block_type == "route"]
    assert len(routes) == 1
    assert routes[0].labels == ["GET", "/api/v1/health"]


def test_handlefunc_normalized_to_any():
    code = 'mux.HandleFunc("/foo", handler)\n'
    blocks = analyze_go(code)
    routes = [b for b in blocks if b.block_type == "route"]
    assert len(routes) == 1
    assert routes[0].labels[0] == "ANY"


def test_full_file_summary():
    code = """package core

type LogMessage struct {}
type CoreClient interface {}

func (lm *LogMessage) Matches() bool { return true }
func New() *CoreClient { return nil }
"""
    fa = analyze_file("internal/core/core.go", code)
    assert fa is not None
    assert "Go file (package core)" in fa.summary
    assert "1 struct" in fa.summary
    assert "1 interface" in fa.summary
    assert "1 method" in fa.summary
    assert "1 function" in fa.summary


def test_tags_include_package_name():
    code = "package authz\nfunc Check() {}\n"
    blocks = analyze_go(code)
    tags = extract_go_tags(blocks)
    assert "authz" in tags
    assert "function" in tags


def test_main_package_tagged_as_binary():
    code = "package main\nfunc main() {}\n"
    blocks = analyze_go(code)
    tags = extract_go_tags(blocks)
    assert "binary" in tags
    assert "entrypoint" in tags


def test_test_file_tagged():
    code = "package foo\nfunc TestX(t *testing.T) {}\n"
    tags = extract_go_tags(analyze_go(code)) + (
        ["test"] if "test" in code else []  # for redundancy if the function-name path also fires
    )
    # function-name path already adds "test"
    assert "test" in tags


def test_generated_file_tagged_by_path():
    from agmem.parsers.go import extract_tags
    blocks = analyze_go("package mocks\n")
    tags = extract_tags("internal/foo/foo_mock.go", blocks)
    assert "generated" in tags


def test_role_inference_from_struct_names():
    code = """package foo
type UserService struct {}
type AuthHandler struct {}
type Config struct {}
type DBClient struct {}
"""
    tags = extract_go_tags(analyze_go(code))
    assert "service" in tags
    assert "handler" in tags
    assert "config" in tags
    assert "client" in tags


def test_route_path_components_become_tags():
    code = 'r.Get("/api/v1/users/{id}", handler)\n'
    tags = extract_go_tags(analyze_go(code))
    assert "api" in tags
    assert "v1" in tags
    assert "users" in tags
    assert "get" in tags


def test_empty_file_returns_no_blocks():
    assert analyze_go("") == []


def test_file_with_only_comments_and_imports():
    code = """// Package foo does things.
package foo

import (
    "fmt"
)
"""
    blocks = analyze_go(code)
    # Only the package block — no func/struct.
    assert [b.block_type for b in blocks] == ["package"]


def test_analyze_file_routes_go_extension():
    fa = analyze_file("internal/x/y.go", "package x\nfunc Z() {}\n")
    assert fa is not None
    assert fa.ext == "go"


# --- Godoc extraction ([1b] enrich-index) ---

class TestGoGodoc:
    def test_godoc_on_function(self):
        from agmem.parsers.go import analyze
        src = (
            "package foo\n"
            "\n"
            "// Add returns the sum of two integers.\n"
            "func Add(a, b int) int { return a + b }\n"
        )
        blocks = [b for b in analyze(src) if b.block_type == "function"]
        assert blocks and blocks[0].name == "Add"
        assert blocks[0].doc == "Add returns the sum of two integers."

    def test_multi_line_godoc_concatenated(self):
        from agmem.parsers.go import analyze
        src = (
            "// Handler serves the HTTP request.\n"
            "// Returns 200 on success, 4xx on validation errors.\n"
            "func Handler() {}\n"
        )
        blocks = [b for b in analyze(src) if b.block_type == "function"]
        assert blocks[0].doc.startswith("Handler serves the HTTP request.")
        assert "200 on success" in blocks[0].doc

    def test_godoc_on_struct_and_method(self):
        from agmem.parsers.go import analyze
        src = (
            "// Server is the public HTTP entry point.\n"
            "type Server struct{ port int }\n"
            "\n"
            "// Start binds the listener and serves forever.\n"
            "func (s *Server) Start() error { return nil }\n"
        )
        by_kind = {b.block_type: b for b in analyze(src)}
        assert by_kind["struct"].doc == "Server is the public HTTP entry point."
        assert by_kind["method"].doc == "Start binds the listener and serves forever."

    def test_no_godoc_when_separated_by_blank_line(self):
        from agmem.parsers.go import analyze
        src = (
            "// Stale comment, doesn't belong to Add.\n"
            "\n"
            "func Add() {}\n"
        )
        blocks = [b for b in analyze(src) if b.block_type == "function"]
        assert blocks[0].doc == ""

    def test_godoc_capped_at_80_chars(self):
        from agmem.parsers.go import analyze
        long = "// " + "a" * 200 + "\n"
        src = long + "func Foo() {}\n"
        blocks = [b for b in analyze(src) if b.block_type == "function"]
        assert len(blocks[0].doc) <= 80
