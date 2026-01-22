"""
Python REPL
"""
# flake8: noqa: F401
import code

# Wildcard imports are preferred to capture most of functionality for usage in REPL
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from time import time
from rich.pretty import pprint

import microcore as mc
from microcore import ui

from ..cli_base import app
from ..constants import *
from ..core import *
from ..gh_api import *
from ..issue_trackers import *
from ..pipeline import *
from ..project_config import *
from ..report_struct import *
from ..utils.cli import *
from ..utils.git import *
from ..utils.git_platform.platform_types import *
from ..utils.git_platform.github import *
from ..utils.git_platform.gitlab import *
from ..utils.git_platform.shared import *
from ..utils.html import *
from ..utils.markdown import *
from ..utils.package_metadata import *
from ..utils.python import *
from ..utils.string import *


@app.command(
    help="Python REPL with core functionality loaded for quick testing/debugging and exploration."
)
def repl():
    code.interact(local=globals())
