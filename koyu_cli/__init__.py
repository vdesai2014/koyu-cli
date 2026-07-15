from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("koyu-cli")
except PackageNotFoundError:          # running from a raw checkout
    __version__ = "0.0.0.dev"
