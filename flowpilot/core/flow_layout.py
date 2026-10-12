"""Flowchart layout for a flat step list (pure geometry, no Qt).

The flat list is parsed into a tree (If/Else, loops, Try/On error) and laid out top-down:
sequences stack vertically, If and Try branches sit side by side and merge at their End
marker, loops show a "repeat" edge from their End marker back to the loop step. Broken
structure (e.g. an Else without If) never fails: the marker is drawn as a normal step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

NODE_W, NODE_H = 260, 62
DECISION_H = 66
END_W, END_H = 132, 32
GAP = 34  # vertical gap between nodes
HGAP = 46  # horizontal gap between branches
EMPTY_W = 90


@dataclass
class Node:
    index: int
    kind: str  # step / decision / loop / try / end
    x: float
    y: float
    w: float
    h: float


@dataclass
class Edge:
    points: list[tuple[float, float]]
    label: str = ""
    style: str = "normal"  # normal / yes / no / error / back


@dataclass
class Box:
    w: float
    h: float
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)

    def shifted(self, dx: float, dy: float) -> tuple[list[Node], list[Edge]]:
        nodes = [Node(n.index, n.kind, n.x + dx, n.y + dy, n.w, n.h) for n in self.nodes]
        edges = [Edge([(x + dx, y + dy) for x, y in e.points], e.label, e.style) for e in self.edges]
        return nodes, edges


# --------------------------------------------------------------------------- tree

@dataclass
class TStep:
    index: int


@dataclass
class TBlock:
    kind: str  # if / loop / try
    index: int
    first: list[Any]
    second: list[Any] | None  # else / catch branch
    second_index: int | None
    end_index: int | None


def build_tree(steps: list[Any]) -> list[Any]:
    """Nested structure of a flat step list (tolerant of broken blocks)."""
    pos = 0

    def parse_seq(stop: set[str]) -> list[Any]:
        nonlocal pos
        seq: list[Any] = []
        while pos < len(steps):
            block = steps[pos].BLOCK
            if block in stop:
                return seq
            if block in ("if", "loop", "try"):
                seq.append(parse_block(block))
            else:
                seq.append(TStep(pos))
                pos += 1
        return seq

    def parse_block(kind: str) -> TBlock:
        nonlocal pos
        start = pos
        pos += 1
        middle = {"if": "else", "try": "catch"}.get(kind)
        end = {"if": "end_if", "loop": "end_loop", "try": "end_try"}[kind]
        stops = {end} | ({middle} if middle else set()) | ({"end_if", "end_loop", "end_try"} - {end})
        first = parse_seq(stops)
        second = second_index = None
        if middle and pos < len(steps) and steps[pos].BLOCK == middle:
            second_index = pos
            pos += 1
            second = parse_seq({end, "end_if", "end_loop", "end_try"} - set())
        end_index = None
        if pos < len(steps) and steps[pos].BLOCK == end:
            end_index = pos
            pos += 1
        return TBlock(kind, start, first, second, second_index, end_index)

    tree: list[Any] = []
    while pos < len(steps):
        tree += parse_seq(set())
        if pos < len(steps):  # a stray end/else marker at top level
            tree.append(TStep(pos))
            pos += 1
    return tree


# --------------------------------------------------------------------------- layout

def _node(index: int, kind: str) -> Box:
    h = DECISION_H if kind == "decision" else NODE_H
    w = END_W if kind == "end" else NODE_W
    if kind == "end":
        h = END_H
    return Box(w, h, [Node(index, kind, 0, 0, w, h)])


def _end(index: int | None) -> Box:
    if index is None:
        return Box(EMPTY_W, 0)
    return _node(index, "end")


def _seq(items: list[Any], steps: list[Any]) -> Box:
    boxes = [_layout(item, steps) for item in items]
    if not boxes:
        return Box(EMPTY_W, 18, [], [Edge([(EMPTY_W / 2, 0), (EMPTY_W / 2, 18)])])
    w = max(b.w for b in boxes)
    out = Box(w, 0)
    y = 0.0
    for i, b in enumerate(boxes):
        if i > 0:
            out.edges.append(Edge([(w / 2, y - GAP), (w / 2, y)]))
        nodes, edges = b.shifted((w - b.w) / 2, y)
        out.nodes += nodes
        out.edges += edges
        y += b.h + GAP
    out.h = y - GAP
    return out


def _branches(head: Box, left: Box, right: Box, end: Box, labels: tuple[str, str],
              styles: tuple[str, str]) -> Box:
    """Head on top, two branches side by side, merging into the end marker."""
    bw = left.w + HGAP + right.w
    w = max(head.w, bw, end.w)
    out = Box(w, 0)
    nodes, edges = head.shifted((w - head.w) / 2, 0)
    out.nodes += nodes
    out.edges += edges
    split_y = head.h + 16
    top = head.h + GAP + 12
    lx = (w - bw) / 2
    rx = lx + left.w + HGAP
    cx = w / 2
    for box, bx, label, style in ((left, lx, labels[0], styles[0]), (right, rx, labels[1], styles[1])):
        bc = bx + box.w / 2
        out.edges.append(Edge([(cx, head.h), (cx, split_y), (bc, split_y), (bc, top)], label, style))
        nodes, edges = box.shifted(bx, top)
        out.nodes += nodes
        out.edges += edges
    merge_y = top + max(left.h, right.h) + 22
    for box, bx in ((left, lx), (right, rx)):
        bc = bx + box.w / 2
        out.edges.append(Edge([(bc, top + box.h), (bc, merge_y), (cx, merge_y)]))
    end_y = merge_y + 14
    out.edges.append(Edge([(cx, merge_y), (cx, end_y)]))
    nodes, edges = end.shifted((w - end.w) / 2, end_y)
    out.nodes += nodes
    out.edges += edges
    out.h = end_y + end.h
    return out


def _layout(item: Any, steps: list[Any]) -> Box:
    if isinstance(item, TStep):
        return _node(item.index, "step")
    if item.kind == "if":
        head = _node(item.index, "decision")
        no = _seq(item.second or [], steps)
        return _branches(head, _seq(item.first, steps), no, _end(item.end_index), ("Yes", "No"), ("yes", "no"))
    if item.kind == "try":
        head = _node(item.index, "try")
        return _branches(head, _seq(item.first, steps), _seq(item.second or [], steps), _end(item.end_index),
                         ("", "On error"), ("normal", "error"))
    # loop: header → body → End loop, with a "repeat" edge back to the header
    head = _node(item.index, "loop")
    body = _seq(item.first, steps)
    end = _end(item.end_index)
    lane = 38
    w = max(head.w, body.w, end.w) + 2 * lane
    cx = w / 2
    out = Box(w, 0)
    for box, y in ((head, 0.0),):
        nodes, edges = box.shifted((w - box.w) / 2, y)
        out.nodes += nodes
        out.edges += edges
    body_y = head.h + GAP
    out.edges.append(Edge([(cx, head.h), (cx, body_y)]))
    nodes, edges = body.shifted((w - body.w) / 2, body_y)
    out.nodes += nodes
    out.edges += edges
    end_y = body_y + body.h + GAP
    out.edges.append(Edge([(cx, body_y + body.h), (cx, end_y)]))
    nodes, edges = end.shifted((w - end.w) / 2, end_y)
    out.nodes += nodes
    out.edges += edges
    left_x = 8.0
    end_mid = end_y + max(end.h, 1) / 2
    head_mid = head.h / 2
    out.edges.append(Edge([((w - end.w) / 2, end_mid), (left_x, end_mid), (left_x, head_mid),
                           ((w - head.w) / 2, head_mid)], "repeat", "back"))
    out.h = end_y + end.h
    return out


def layout_flow(steps: list[Any]) -> Box:
    """Lay out the whole flow with a Start and an End terminal."""
    body = _seq(build_tree(steps), steps)
    w = max(body.w, END_W)
    out = Box(w, 0)
    out.nodes.append(Node(-1, "start", (w - END_W) / 2, 0, END_W, END_H))
    y = END_H + GAP
    out.edges.append(Edge([(w / 2, END_H), (w / 2, y)]))
    nodes, edges = body.shifted((w - body.w) / 2, y)
    out.nodes += nodes
    out.edges += edges
    y += body.h + GAP
    out.edges.append(Edge([(w / 2, y - GAP), (w / 2, y)]))
    out.nodes.append(Node(-2, "finish", (w - END_W) / 2, y, END_W, END_H))
    out.h = y + END_H
    return out
