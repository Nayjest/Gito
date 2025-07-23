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
from ..utils import *
from ..gh_api import *


@app.command(help="python REPL")
def repl():
    code.interact(local=globals())
