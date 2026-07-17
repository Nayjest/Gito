"""Deprecated alias — the CodePulse package is now ``codepulse_app``.

This shim keeps ``python -m code_doctor_app`` and ``import code_doctor_app``
working for existing launchers and scripts. New code should import
``codepulse_app`` directly.
"""
from __future__ import annotations

import importlib


def __getattr__(name: str):
    # ``code_doctor_app.server`` etc. resolve to the canonical modules, so a
    # process never ends up with two instances of the same module state.
    return importlib.import_module(f"codepulse_app.{name}")
