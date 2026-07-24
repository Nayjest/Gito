"""Recall expansion (v5.1): destructive filesystem, deserialization, SSTI, and
shell sinks added to the taint engine, plus a false-positive guard."""
from __future__ import annotations

import glob

import pytest

from codepulse_app import taint_analysis as ta


def rules(findings):
    return [f["rule"] for f in findings]


@pytest.mark.parametrize(
    "body, expect_rule",
    [
        ("import os\n    os.remove(p)", "taint-path-traversal"),
        ("import os\n    os.unlink(p)", "taint-path-traversal"),
        ("import os\n    os.rename(p, '/tmp/x')", "taint-path-traversal"),
        ("import shutil\n    shutil.rmtree(p)", "taint-path-traversal"),
        ("import shutil\n    shutil.move(p, '/tmp/x')", "taint-path-traversal"),
        ("import yaml\n    yaml.load(p)", "taint-deserialization"),
        ("import marshal\n    marshal.loads(p)", "taint-deserialization"),
        ("import subprocess\n    subprocess.getoutput(p)", "taint-command-injection"),
        ("import subprocess\n    subprocess.getstatusoutput(p)", "taint-command-injection"),
    ],
)
def test_new_sinks_fire_on_tainted_input(body, expect_rule):
    src = f"from flask import request\ndef f():\n    p = request.args['x']\n    {body}\n"
    assert expect_rule in rules(ta.analyze_source(src))


def test_pathlib_unlink_receiver_is_a_delete_sink():
    src = (
        "from flask import request\n"
        "from pathlib import Path\n"
        "def f():\n"
        "    Path(request.args['p']).unlink()\n"
    )
    assert "taint-path-traversal" in rules(ta.analyze_source(src))


def test_jinja_from_string_is_ssti():
    src = (
        "from flask import request\n"
        "def f(env):\n"
        "    return env.from_string(request.args['t']).render()\n"
    )
    assert "taint-template-injection" in rules(ta.analyze_source(src))


def test_new_sinks_do_not_fire_on_constant_paths():
    # Same sinks, but the argument is a literal — must stay quiet.
    src = (
        "import os, shutil, yaml, subprocess\n"
        "def f():\n"
        "    os.remove('/tmp/fixed')\n"
        "    shutil.rmtree('/tmp/dir')\n"
        "    yaml.load(open('config.yml'))\n"
        "    subprocess.getoutput('ls -la')\n"
    )
    assert ta.analyze_source(src) == []


def test_self_scan_stays_clean_with_expanded_sinks():
    """CodePulse's own source uses os.remove / shutil / Path.unlink / subprocess
    with trusted, non-request paths — the expanded sink set must not flag them."""
    findings = []
    for path in glob.glob("codepulse_app/*.py"):
        with open(path, encoding="utf-8") as handle:
            findings += ta.analyze_source(handle.read(), path)
    assert findings == [], [f"{f['file']}:{f['line']} {f['title']}" for f in findings]
