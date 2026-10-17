#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""沪上错题 变式图示生成 · 数学（matplotlib，经运行验证）。
用法：from fig_math import parabola; parabola("/tmp/a.png", -1, 2, 3, points=[(0,3,"C"),(3,4,"顶点")])
"""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

plt.rcParams["font.sans-serif"] = ["PingFang SC", "STHeiti", "SimHei",
                                   "Microsoft YaHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

def parabola(path, a, b, c, points=None, xmin=None, xmax=None):
    fig, ax = plt.subplots(figsize=(5, 4))
    xs = np.linspace(xmin if xmin is not None else -4,
                     xmax if xmax is not None else 6, 300)
    ax.plot(xs, a*xs**2 + b*xs + c, "b-", lw=2,
            label=f"y={a}x²+{b}x+{c}")
    if points:
        for px, py, lab in points:
            ax.plot(px, py, "ro", ms=7)
            ax.annotate(lab, (px, py), textcoords="offset points",
                        xytext=(6, -12), fontsize=11)
    ax.axhline(0, color="k", lw=.6); ax.axvline(0, color="k", lw=.6)
    ax.grid(alpha=.3); ax.legend(fontsize=9); fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight"); plt.close(fig)
    return path

def line_pair(path, segs, x_range=(-2, 5)):
    """segs: [(k, m, label)] 直线 y=kx+m"""
    fig, ax = plt.subplots(figsize=(5, 4))
    xs = np.linspace(*x_range, 200)
    for k, m, lab in segs:
        ax.plot(xs, k*xs + m, lw=2, label=lab)
    ax.axhline(0, color="k", lw=.6); ax.axvline(0, color="k", lw=.6)
    ax.grid(alpha=.3); ax.legend(fontsize=9); fig.tight_layout()
    fig.savefig(path, dpi=150, bbox_inches="tight"); plt.close(fig)
    return path

if __name__ == "__main__":
    print(parabola("/tmp/fig_demo.png", -1, 2, 3, points=[(0, 3, "C"), (1, 4, "顶点")]))
