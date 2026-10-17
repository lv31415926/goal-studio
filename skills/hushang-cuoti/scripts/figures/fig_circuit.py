#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""沪上错题 变式图示生成 · 物理电路图（matplotlib 简化符号，经运行验证）。
用法：from fig_circuit import circuit_rc; circuit_rc("/tmp/c.png")
schemdraw 可用时优先 schematic()；缺库自动回退 matplotlib。
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, Rectangle
import numpy as np

plt.rcParams["font.sans-serif"] = ["PingFang SC", "STHeiti", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

def circuit_rc(path, r1="R1", r2="R2", switch_open=True):
    fig, ax = plt.subplots(figsize=(6, 4))
    ax.set_xlim(0, 10); ax.set_ylim(0, 6); ax.axis("off")
    # 电源
    ax.plot([0.8, 0.8], [2.4, 3.6], "k-", lw=3); ax.plot([1.2, 1.2], [2.7, 3.3], "k-", lw=6)
    ax.text(1.0, 2.0, "电源", ha="center", fontsize=9)
    # 主回路
    ax.plot([0.8, 0.8], [3.0, 5.0], "k-", lw=1.5)
    ax.plot([0.8, 3.2], [5.0, 5.0], "k-", lw=1.5)
    # 开关
    ax.plot([3.2, 3.9], [5.0, 5.0 + (0.4 if switch_open else 0)], "k-", lw=1.5)
    ax.plot([3.2, 3.2], [4.85, 5.15], "ko", ms=4); ax.plot([3.9, 3.9], [4.85, 5.15], "ko", ms=4)
    ax.text(3.55, 5.55, "S", ha="center", fontsize=10)
    ax.plot([3.9, 5.0], [5.0, 5.0], "k-", lw=1.5)
    # R1
    ax.add_patch(Rectangle((5.0, 4.75), 1.6, 0.5, lw=1.5, fill="white", edgecolor="k"))
    ax.text(5.8, 5.45, r1, ha="center", fontsize=10)
    ax.plot([6.6, 9.0], [5.0, 5.0], "k-", lw=1.5)
    ax.plot([9.0, 9.0], [5.0, 3.0], "k-", lw=1.5)
    # 并联支路 R2
    ax.plot([4.0, 4.0], [5.0, 3.0], "k--", lw=1)
    ax.add_patch(Rectangle((3.2, 2.75), 1.6, 0.5, lw=1.5, fill="white", edgecolor="k"))
    ax.text(4.0, 2.45, r2, ha="center", fontsize=10)
    # 电流表干路
    ax.add_patch(Circle((7.8, 3.0), 0.3, fill=False, lw=1.5))
    ax.text(7.8, 3.0, "A", ha="center", va="center", fontsize=10)
    ax.plot([1.2, 7.5], [3.0, 3.0], "k-", lw=1.5)
    ax.plot([8.1, 9.0], [3.0, 3.0], "k-", lw=1.5)
    ax.plot([1.2, 1.2], [3.0, 2.4], "k-", lw=1.5)
    ax.plot([4.0, 4.0], [3.0, 3.25], "k--", lw=1)
    fig.tight_layout(); fig.savefig(path, dpi=150, bbox_inches="tight"); plt.close(fig)
    return path

if __name__ == "__main__":
    print(circuit_rc("/tmp/circuit_demo.png"))
