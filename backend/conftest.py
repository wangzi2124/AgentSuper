# -*- coding: utf-8 -*-
"""pytest 引导：确保 backend 根目录在 sys.path 上，`from app...` 可直接导入。"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))