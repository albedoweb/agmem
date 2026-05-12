"""Tests for the Python parser (E1+E2)."""

from agmem.content_analyzer import (
    analyze_file,
    analyze_py,
    extract_py_tags,
    extract_tags_for_file,
)


def test_top_level_class():
    code = """
class BillingPlanID(StrEnum):
    PRO = "pro"
    PRO_YEAR = "pro_year"
"""
    blocks = analyze_py(code)
    assert len(blocks) == 1
    assert blocks[0].block_type == "class"
    assert blocks[0].name == "BillingPlanID"
    assert blocks[0].labels == ["StrEnum"]


def test_class_with_multiple_bases_and_generics():
    code = "class Foo(BaseModel, Generic[T], metaclass=Meta):\n    pass"
    blocks = analyze_py(code)
    assert blocks[0].name == "Foo"
    # Generic params stripped, kwargs reduced to value side
    assert "BaseModel" in blocks[0].labels
    assert "Generic" in blocks[0].labels
    assert "Meta" in blocks[0].labels


def test_class_no_bases():
    code = "class Bare:\n    pass"
    blocks = analyze_py(code)
    assert blocks[0].name == "Bare"
    assert blocks[0].labels == []


def test_top_level_function_captured():
    code = """
def public_helper(x):
    return x

async def fetch_thing():
    pass

class Foo:
    def method_should_not_appear(self):
        pass
"""
    blocks = analyze_py(code)
    fns = [b for b in blocks if b.block_type == "function"]
    names = [b.name for b in fns]
    assert "public_helper" in names
    assert "fetch_thing" in names
    assert "method_should_not_appear" not in names


def test_fastapi_route_decorator():
    code = """
@router.get("/v1/billing/plans")
async def get_billing_plans():
    return {}
"""
    blocks = analyze_py(code)
    routes = [b for b in blocks if b.block_type == "route"]
    assert len(routes) == 1
    assert routes[0].name == "get_billing_plans"
    assert routes[0].labels == ["GET", "/v1/billing/plans"]


def test_post_route_with_path_params():
    code = """
@router.post("/v1/users/{user_id}/promo")
async def attach_promo(user_id: str):
    pass
"""
    blocks = analyze_py(code)
    assert blocks[0].block_type == "route"
    assert blocks[0].labels == ["POST", "/v1/users/{user_id}/promo"]


def test_route_decorator_with_extra_decorators_in_between():
    code = """
@router.get("/health")
@some_other_decorator
async def health_check():
    pass
"""
    blocks = analyze_py(code)
    routes = [b for b in blocks if b.block_type == "route"]
    assert len(routes) == 1
    assert routes[0].name == "health_check"


def test_pending_route_clears_on_unrelated_code():
    code = """
@router.get("/orphan")
x = 5

def unrelated_function():
    pass
"""
    blocks = analyze_py(code)
    # Orphan decorator gets dropped because next non-deco line was assignment.
    assert all(b.block_type != "route" for b in blocks)
    assert any(b.block_type == "function" and b.name == "unrelated_function" for b in blocks)


def test_extract_py_tags_routes():
    blocks = analyze_py("""
@router.get("/v1/billing/plans")
async def get_plans():
    pass
""")
    tags = extract_py_tags(blocks)
    assert "route" in tags
    assert "get" in tags
    assert "api" in tags
    assert "billing" in tags
    assert "plans" in tags


def test_extract_py_tags_models():
    blocks = analyze_py("""
class User(Document):
    pass

class UserSchema(BaseModel):
    pass

class Status(StrEnum):
    ACTIVE = "active"
""")
    tags = extract_py_tags(blocks)
    assert "class" in tags
    assert "model" in tags  # from Document
    assert "mongodb" in tags
    assert "schema" in tags  # from BaseModel
    assert "pydantic" in tags
    assert "enum" in tags  # from StrEnum


def test_extract_tags_for_file_dispatches_by_ext():
    blocks = analyze_py("class Foo(BaseModel): pass")
    py_tags = extract_tags_for_file("a.py", blocks)
    tf_tags = extract_tags_for_file("a.tf", blocks)
    other = extract_tags_for_file("a.unknown", blocks)
    assert "schema" in py_tags
    assert "schema" not in tf_tags
    assert other == []


def test_analyze_file_python():
    code = """
@router.get("/health")
async def health():
    pass

class User(Document):
    pass

def helper():
    pass
"""
    analysis = analyze_file("api.py", code)
    assert analysis is not None
    assert analysis.ext == "py"
    assert "1 route" in analysis.summary
    assert "1 class" in analysis.summary
    assert "1 function" in analysis.summary


def test_analyze_file_python_empty_returns_none():
    assert analyze_file("empty.py", "x = 1\n# nothing top-level\n") is None
