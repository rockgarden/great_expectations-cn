from great_expectations.compatibility.not_imported import NotImported

PY4J_NOT_IMPORTED = NotImported("py4j is not installed, please 'pip install py4j'")

try:
    from py4j import protocol  # type: ignore[import-untyped] # FIXME CoP
except ImportError:
    protocol = PY4J_NOT_IMPORTED
