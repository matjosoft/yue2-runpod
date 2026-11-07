"""Worker application factory.

M0 skeleton: no routes yet. Transport, jobs, operations and engines arrive in M1.
"""

from fastapi import FastAPI

from studio_worker import __version__


def create_app() -> FastAPI:
    return FastAPI(title="Studio worker", version=__version__)
