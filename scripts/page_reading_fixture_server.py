"""Disposable test mount only. Actual backend API/owner, no API substitutions."""
import os,sys
from pathlib import Path
sys.path.insert(0,str(Path(os.environ['PAGE_PROBE_SOURCE'])/'backend'))
import app as backend
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles
backend.app.router.routes.insert(0,Mount('/__probe__',app=StaticFiles(directory=os.environ['PAGE_PROBE_COMPONENT_DIST'],html=True)))
app=backend.app
