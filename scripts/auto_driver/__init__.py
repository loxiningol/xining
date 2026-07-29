# -*- coding: utf-8 -*-
"""Autonomous Gate-retry driver (wrapper only — does not modify STEP A core).

Package layout:
  scripts/auto_driver/          library
  scripts/run_auto_driver.py    CLI entry
  scripts/run_auto_driver.sh    one-click shell
  scripts/auto_driver_config.example.json
"""
from __future__ import print_function

from .driver import run_driver

__all__ = ["run_driver"]
