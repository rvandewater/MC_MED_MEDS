from importlib.metadata import PackageNotFoundError, version

__package_name__ = "MC-MED-MEDS"

try:
    __version__ = version(__package_name__)
except PackageNotFoundError:  # pragma: no cover
    __version__ = "unknown"

__all__ = ["__package_name__", "__version__"]
