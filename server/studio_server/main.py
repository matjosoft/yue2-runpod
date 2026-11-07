"""Local server application factory.

M0 skeleton: no routes yet. Config, core modules and feature modules arrive in M2.
"""

from fastapi import FastAPI

from studio_server import __version__


def create_app() -> FastAPI:
    return FastAPI(title="Studio server", version=__version__)
