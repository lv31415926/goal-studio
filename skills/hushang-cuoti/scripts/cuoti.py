#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""沪上错题 · 确定性引擎 v1.1（仅标准库）

职责边界：查重/存储/排期/状态机/统计/未学先练检测。识别、归因、判分语义、变式、复核
由会话模型完成，本脚本只做事实性读写。数据目录 $CUOTI_HOME（缺省 ~/.hushang-cuoti）。
JSONL append-only；重写走临时文件+原子 rename。多学生按 student 字段全链路隔离。
"""
import argparse, hashlib, json, os, re, shutil, sys, uuid
from datetime import date, datetime, timedelta
from pathlib import Path

HOME = Path(os.environ.get("CUOTI_HOME", Path.home() / ".hushang-cuoti"))
SKILL_DIR = Path(__file__).resolve().parent.parent
FILES = {"questions": "questions.jsonl", "wrong": "wrong-answers.jsonl",
         "log": "review-log.jsonl", "variants": "variants.jsonl",
         "profiles": "profiles.jsonl", "exams": "exams.jsonl"}
W = {"unified": 1.0, "graded": 1.5, "pass_only": 0.5}        # schedule-spec §3
INT = [1, 3]                                                  # 常规间隔（首排1，pass后3）
WAKE = [30, 90, 180]                                          # 远期唤醒阶梯
MEMO = [1, 2, 4, 8, 16]                                       # 记忆型档
SUBJ_FILES = {"数学": "math.md", "英语": "english.md", "物理": "physics.md",
              "化学": "chemistry.md"}

def now(): return datetime.now().isoformat(timespec="seconds")
def today(): return date.today()
def dparse(s):
    try: return date.fromisoformat((s or "")[:10])
    except Exception: return None
def load(key):
    p = HOME / FILES[key]
    if not p.exists(): return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try: out.append(json.loads(line))
            except json.JSONDecodeError: continue
    return out
def append(key, rec):
    p = HOME / FILES[key]; p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
def rewrite(key, recs):
    p = HOME / FILES[key]; tmp = p.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs),
                   encoding="utf-8")
    tmp.replace(p)
def norm(text):
    t = re.sub(r"[\s\u3000]+", "", text or "")
    return t.translate(str.maketrans("（）：；，。！？【】ＡＢＣ", "():;,.!?[]ABC")).lower()
def fp_text(text): return hashlib.sha256(norm(text).encode("utf-8")).hexdigest()[:16]
def fp_img(path):
    if not path: return None
    p = Path(path)
    if not p.is_file(): return None
    return hashlib.md5(p.read_bytes()).hexdigest()
def bigrams(text):
    n = norm(text); return {n[i:i+2] for i in range(len(n)-1)} if len(n) > 1 else {n}
def jac(a, b):
    A, B = bigrams(a), bigrams(b)
    return len(A & B) / len(A | B) if A | B else 0.0
def cfg():
    p = HOME / "config.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
def fail(obj, code=1):
    print(json.dumps(obj, ensure_ascii=False)); raise SystemExit(code)
def stu_of(c, it=None):
    return (it or {}).get("student") or c.get("student", "default")

_kp_cache = {}
def _anchor_rows(path):
    rows = _kp_cache.get(str(path))
    if rows is None:
        rows = {}
        if path.exists():
            body = path.read_text(encoding="utf-8").split("## 课标映射")[0]  # 主表区，映射节不参与锚点解析
            for line in body.splitlines():
                parts = [p.strip() for p in line.split("|")]
                if len(parts) > 3 and re.fullmatch(
                        r"(?:[MEPC]\d{2}|E-[A-Z]{3}|B-[A-Z]{3})", parts[1]):
                    rows[parts[1]] = parts[3]
        _kp_cache[str(path)] = rows
    return rows
def kp_anchor(subj, kp):
    f = SKILL_DIR / "references" / "subjects" / SUBJ_FILES.get(subj, "")
    return _anchor_rows(f).get(kp)
def anchor_max_module(anchor):
    """锚点列→最高册次序号：必修一=1 必修二=2 必修三=3 选必1..3=4..6；跨/无=0。"""
    if not anchor: return 0
    a = re.sub(r"选择性必修[一二三123]|选必[一二三123]", lambda m: "@" + m.group()[-1],
               anchor)  # @一/@1 标记选必，避免与必修混淆
    idx = 0
    for pat, n in (("必修一", 1), ("必修二", 2), ("必修三", 3)):
        if pat in a: idx = max(idx, n)
    for pat, n in (("@一", 4), ("@二", 5), ("@三", 6), ("@1", 4), ("@2", 5), ("@3", 6)):
        if pat in a: idx = max(idx, n)
    if "@" in a and idx < 4: idx = 4        # "选必"无编号按选必一计
    return idx
def term_threshold(grade):
    g = str(grade or "")
    if "高一上" in g: return 1
    if "高一下" in g: return 2
    if "高一" in g: return 1                 # 保守默认；提示用户写"高一上/下"
    if "高二上" in g: return 3
    if "高二下" in g: return 6
    if "高二" in g: return 3
    if "高三" in g: return 6
    return 0
def unlearned_kps(c, subj, kps):
    """学生学期 × 知识块教材锚点 → 未学先练预警（册次口径按 subjects 表核实注记）。"""
    th = term_threshold(c.get("grade"))
    if th == 0: return []
    out = []
    for k in kps:
        if str(k).startswith("grow:"): continue
        m = anchor_max_module(kp_anchor(subj, k))
        if m > th: out.append(k)
    return out

def cmd_init(a):
    (HOME / "media").mkdir(parents=True, exist_ok=True)
    (HOME / "reports" / "archive").mkdir(parents=True, exist_ok=True)
    p = HOME / "config.json"
    if not p.exists():
        p.write_text(json.dumps({"student": "default", "grade": "未设置",
                                 "exam_mode": {}, "targets": {}, "next_exam": {},
                                 "push_enabled": False, "policy_year": "未核对"},
                                ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"home": str(HOME), "status": "ok"}, ensure_ascii=False))

def cmd_set(a):
    c = cfg()
    for kv in a.pairs:
        k, _, v = kv.partition("=")
        try: c[k] = json.loads(v)
        except json.JSONDecodeError: c[k] = v
    (HOME / "config.json").write_text(json.dumps(c, ensure_ascii=False, indent=2),
                                      encoding="utf-8")
    print(json.dumps(c, ensure_ascii=False))

def cmd_dedup(a):
    c = cfg(); stu = a.student or c.get("student", "default")
    tfp = fp_text(a.text) if a.text else None
    ipf = fp_img(a.image) if a.image else None
    hits = []
    for w in load("wrong"):
        if w.get("student", "default") != stu: continue
        if (ipf and w.get("image_md5") == ipf) or (tfp and w.get("text_fp") == tfp):
            hits.append({"id": w["id"], "sim": 1.0, "why": "exact"})
        elif tfp and w.get("question_norm"):
            s = jac(a.text, w["question_norm"])
            if s > 0.85: hits.append({"id": w["id"], "sim": round(s, 2), "why": "fuzzy"})
    print(json.dumps({"duplicate": bool(hits), "hits": hits[:5]}, ensure_ascii=False))

def cmd_add(a):
    intake = json.loads(Path(a.file).read_text(encoding="utf-8"))
    items = intake if isinstance(intake, list) else [intake]
    c = cfg(); added, dups, held, unlearned = [], [], [], []
    stu = stu_of(c)
    pool = [w for w in load("wrong") if w.get("student", "default") == stu]
    qpool = [q for q in load("questions") if q.get("student", "default") == stu]
    existing_fp = {w.get("text_fp") for w in pool} | {q.get("text_fp") for q in qpool}
    existing_img = {w.get("image_md5") for w in pool}
    for it in items:
        s_stu = stu_of(c, it)
        q = it.get("question", {}); text = q.get("text", "")
        tfp, ipf = fp_text(text), fp_img(it.get("image", ""))
        # 查重：文本指纹命中，或 同图且题面近似>0.85（同图多题是合法场景，纯图md5不判重）
        dup = tfp in existing_fp or (ipf and ipf in existing_img and
              any(w.get("image_md5") == ipf and jac(text, w.get("question_norm", "")) > 0.85
                  for w in pool))
        if dup and not a.force:
            dups.append({"text": text[:30], "why": "duplicate"}); continue
        wid = "wa-" + uuid.uuid4().hex[:12]
        media_copy = None
        if it.get("image") and Path(it["image"]).is_file():
            mdir = HOME / "media"; mdir.mkdir(parents=True, exist_ok=True)
            ext = Path(it["image"]).suffix or ".img"
            media_copy = str(mdir / (wid + ext))
            shutil.copy2(it["image"], media_copy)
        qid = "q-" + uuid.uuid4().hex[:12]
        append("questions", {"id": qid, "student": s_stu, "text": text, "text_fp": tfp,
                             "subject": it.get("subject"), "qtype": q.get("qtype"),
                             "kp_ids": q.get("kp_ids", []), "image_md5": ipf,
                             "difficulty": q.get("difficulty_self"),
                             "exam_track": it.get("exam_track"), "created_at": now()})
        memo = any(str(k).startswith(("B-VOC", "B-GRM")) for k in q.get("kp_ids", []))
        need_conf = bool(it.get("needs_confirmation"))
        ul = unlearned_kps(c, it.get("subject"), q.get("kp_ids", []))
        rec = {"id": wid, "question_id": qid, "student": s_stu,
               "subject": it.get("subject"), "exam_track": it.get("exam_track"),
               "source_type": it.get("source_type", "own_exam"),
               "question_text": text, "question_norm": norm(text), "text_fp": tfp,
               "image": it.get("image"), "image_md5": ipf, "media_copy": media_copy,
               "student_answer": it.get("student_answer", ""),
               "correct_answer": it.get("correct_answer", ""),
               "kp_ids": q.get("kp_ids", []), "wrong": it.get("wrong", {}),
               "score_axis": it.get("wrong", {}).get("score_axis", {}),
               "exam_paper_id": it.get("exam_paper_id"), "qno": it.get("qno"),
               "points_max": it.get("points_max"), "points_got": it.get("points_got"),
               "unlearned_flags": ul,
               "memory_type": "memo" if memo else "standard",
               "origin": it.get("origin", "real"),
               "state": "hold" if need_conf else ("watch" if it.get("origin") == "watch" else "scheduled"),
               "needs_confirmation": it.get("needs_confirmation", []),
               "pass_streak": 0, "fail_streak": 0, "interval_idx": 0, "wake_idx": -1,
               "next_due": None if (need_conf or it.get("origin") == "watch") else
                   str(today() + timedelta(days=(MEMO if memo else INT)[0])),
               "created_at": now()}
        append("wrong", rec); existing_fp.add(tfp)
        if ipf: existing_img.add(ipf)
        (held if need_conf else added).append(
            {"id": wid, "subject": rec["subject"], "kp": rec["kp_ids"],
             "due": rec["next_due"], "confirms": rec["needs_confirmation"],
             "unlearned": ul})
        if ul: unlearned.append({"id": wid, "kps": ul,
                                 "note": "知识块册次高于学生学期，先核对教学进度"})
    print(json.dumps({"added": added, "held_pending_confirmation": held,
                      "skipped_duplicate": dups, "unlearned_flags": unlearned},
                     ensure_ascii=False, indent=1))

def _find(wid):
    c = cfg(); stu = c.get("student", "default")
    return next((w for w in load("wrong")
                 if w["id"] == wid and w.get("student", "default") == stu), None)
def _upd_wid(wid, patch):
    pool = load("wrong"); hit = False
    for i, w in enumerate(pool):
        if w["id"] == wid:
            pool[i] = {**w, **patch, "updated_at": now()}; hit = True; break
    if not hit: fail({"error": f"{wid} not found"})
    rewrite("wrong", pool); return pool[i]

def cmd_confirm(a):
    if not _find(a.id): fail({"error": "not found for current student", "advance": False})
    w = _upd_wid(a.id, {"state": "scheduled", "needs_confirmation": [],
                        "next_due": str(today() + timedelta(days=1))})
    print(json.dumps({"id": a.id, "state": w["state"], "due": w["next_due"]},
                     ensure_ascii=False))

def cmd_grade(a):
    w = _find(a.id) or fail({"error": "not found"})
    if w.get("state") == "hold":
        fail({"error": "hold态须先confirm，禁止判分", "advance": False})
    if w.get("state") == "archived":
        fail({"error": "archived 为终态静默；再练请走变式/重新录入，grade 不接受", "advance": False})
    r, kp = a.result, a.keypoints
    rec = {"wa_id": a.id, "kind": "grade", "result": r, "keypoints": kp, "at": now(),
           "basis": a.basis or ""}
    if r == "browse":
        append("log", {**rec, "advance": False})
        print(json.dumps({"note": "浏览不计掌握，状态未推进", "state": w["state"]},
                         ensure_ascii=False)); return
    if r == "pending_confirmation":
        append("log", {**rec, "advance": False})
        print(json.dumps({"note": "已记录待人工确认，未销号未排期", "state": w["state"]},
                         ensure_ascii=False)); return
    patch = {}
    if r == "pass" and kp == "ok":
        streak = w.get("pass_streak", 0) + 1
        if streak >= 2:
            patch = {"state": "mastered", "pass_streak": streak, "fail_streak": 0,
                     "wake_idx": 0,
                     "next_due": str(today() + timedelta(days=WAKE[0]))}
        else:
            gaps = MEMO if w.get("memory_type") == "memo" else INT
            gap = gaps[min(w.get("interval_idx", 0) + 1, len(gaps) - 1)]
            patch = {"state": "scheduled", "pass_streak": streak, "fail_streak": 0,
                     "interval_idx": w.get("interval_idx", 0) + 1,
                     "next_due": str(today() + timedelta(days=gap))}
    elif r == "pass" and kp in ("bad", "partial"):   # pass-but-broken：假掌握防线
        patch = {"state": "reviewing", "pass_streak": 0,
                 "fail_streak": w.get("fail_streak", 0), "interval_idx": 0,
                 "next_due": str(today() + timedelta(days=1))}
    elif r == "fail":
        fs = w.get("fail_streak", 0) + 1
        patch = {"state": "escalated" if fs >= 2 else "scheduled",
                 "fail_streak": fs, "pass_streak": 0, "interval_idx": 0,
                 "next_due": str(today() + timedelta(days=1))}
    elif r == "wake_pass":
        wi = w.get("wake_idx", 0) + 1
        if wi < len(WAKE):
            patch = {"state": "mastered", "wake_idx": wi,
                     "next_due": str(today() + timedelta(days=WAKE[wi]))}
        else:
            patch = {"state": "archived", "wake_idx": wi, "next_due": None}
    else:
        fail({"error": f"unknown result {r}"})
    if w.get("origin") == "demo":
        append("log", {**rec, "advance": False, "note": "demo不推进(防污染)"})
        print(json.dumps({"note": "origin=demo 不计入掌握", "state": w["state"]},
                         ensure_ascii=False)); return
    append("log", {**rec, "advance": True, "was": w["state"],
                   "to": patch.get("state", w["state"])})
    _upd_wid(a.id, patch)
    out = _find(a.id)
    note = "pass-but-broken：结果对路径歪，连对归零重置1天——重练骨架" \
        if r == "pass" and kp in ("bad", "partial") else ""
    print(json.dumps({"id": a.id, "state": out["state"], "next_due": out["next_due"],
                      "pass_streak": out["pass_streak"], "fail_streak": out["fail_streak"],
                      "note": note}, ensure_ascii=False))

def band_points(w):
    try: return float(w.get("score_axis", {}).get("points", 0) or 0)
    except (TypeError, ValueError): return 0.0

def _plan_recs(c, subject=None, exam_mode=False):
    emap = c.get("exam_mode", {}); stu = c.get("student", "default")
    out = []
    for w in load("wrong"):
        if w.get("origin") == "demo" or w.get("state") == "hold": continue
        if w.get("student", "default") != stu: continue
        if subject and w.get("subject") != subject: continue
        mode = emap.get(w.get("subject"), "unified")
        if mode == "unlisted": continue
        if mode == "pass_only" and not exam_mode: continue
        d = dparse(w.get("next_due")); st = w.get("state")
        due_now = d is not None and d <= today()
        if st in ("scheduled", "reviewing", "escalated") and due_now:
            overdue = min(max(0.0, (today() - d).days / 7), 2.0)
            pts = band_points(w)
            bonus = min(0.5, 0.5 * pts / 3) if mode == "graded" else 0
            score = W.get(mode, 1.0) * (1 + w.get("fail_streak", 0)) * (1 + overdue) + bonus
            out.append({"rec": w, "mode": mode, "score": score,
                        "escalated": st == "escalated"})
        if exam_mode and st == "watch":   # watch 型仅考前出现一次
            out.append({"rec": w, "mode": mode, "score": 0.1, "escalated": False,
                        "watch": True})
        if exam_mode and st == "mastered" and due_now:
            out.append({"rec": w, "mode": mode,
                        "score": W.get(mode, 1.0), "escalated": False, "wake": True})
    out.sort(key=lambda x: (not x["escalated"], -x["score"]))
    return out

def cmd_plan(a):
    c = cfg(); items = _plan_recs(c, a.subject, a.exam_mode)[:a.limit]
    rows = [{"id": x["rec"]["id"], "subject": x["rec"]["subject"],
             "kp": x["rec"]["kp_ids"], "state": x["rec"]["state"],
             "escalated": x["escalated"], "wake": x.get("wake", False),
             "band_points": band_points(x["rec"]),
             "band_desc": x["rec"].get("score_axis", {}).get("desc", ""),
             "unlearned": x["rec"].get("unlearned_flags", []),
             "breakpoint": x["rec"].get("wrong", {}).get("breakpoint", ""),
             "review_mode": "唤醒重做" if x.get("wake") else "遮答案重做"} for x in items]
    print(json.dumps({"date": str(today()), "count": len(rows), "items": rows,
                      "rule": "呈现时只用题面文本(答案段不显示)；browse不推进状态机"},
                     ensure_ascii=False, indent=1))

def cmd_similar(a):
    c = cfg(); stu = c.get("student", "default")
    w = _find(a.id) or fail({"error": "not found"})
    cands, mine = [], set(w.get("kp_ids", []))
    for q in load("questions"):
        if q.get("student", "default") != stu: continue
        if q["id"] == w.get("question_id"): continue
        s = 0.55 * jac(w["question_text"], q.get("text", "")) \
            + 0.45 * (len(mine & set(q.get("kp_ids", []))) > 0)
        if s > 0.3: cands.append({"id": q["id"], "score": round(s, 2),
                                  "text": q.get("text", "")[:60],
                                  "kp": q.get("kp_ids", [])})
    cands.sort(key=lambda x: -x["score"])
    print(json.dumps({"example": a.id, "matches": cands[:a.top],
                      "hint": "不足则走变式生成(templates/variant-prompt.md)"},
                     ensure_ascii=False, indent=1))

def cmd_stats(a):
    c = cfg(); since = today() - timedelta(days=a.days)
    stu = c.get("student", "default")
    all_wrong = [w for w in load("wrong") if w.get("id")]
    pool = [w for w in all_wrong if w.get("origin") != "demo"
            and w.get("student", "default") == stu]
    pool_ids = {w["id"] for w in pool}
    # 判分流水与卡池 join：同时获得 学生隔离 + demo排除 + 未推进(advance=False)排除（L3审计N2）
    lg = [x for x in load("log") if x.get("kind", "grade") == "grade"
          and x.get("wa_id") in pool_ids and x.get("advance") is not False]
    def recent(items, key="created_at"):
        return [x for x in items if (dparse(x.get(key)) or today()) >= since]
    bysub, byx, clusters = {}, {}, {}
    open_cost = {}
    for w in pool:
        s = w.get("subject", "?"); bysub[s] = bysub.get(s, 0) + 1
        for t in (w.get("wrong", {}).get("x_tags")
                  or ([w["wrong"]["x_primary"]] if w.get("wrong", {}).get("x_primary") else [])):
            byx[t] = byx.get(t, 0) + 1
        pts = band_points(w)
        if w.get("state") not in ("mastered", "archived"):
            open_cost[s] = round(open_cost.get(s, 0) + pts, 2)   # 每卡一次，不按kp重复（L3审计N1）
        for k in w.get("kp_ids", []):
            key = f"{s}·{k}"
            e = clusters.setdefault(key, {"n": 0, "band_points": 0.0})
            e["n"] += 1
            if w.get("state") not in ("mastered", "archived"):
                e["band_points"] = round(e["band_points"] + pts, 2)  # 簇视角允许同卡计入多簇
    states = {}
    for w in pool:
        st = w.get("state", "?"); states[st] = states.get(st, 0) + 1
    rp = recent(lg, "at")
    cleared = len([x for x in rp if x.get("result") == "pass"
                   and x.get("keypoints") == "ok"])
    corrections = len([x for x in rp if x.get("result") == "pass"])
    top = dict(sorted(clusters.items(), key=lambda kv: -kv[1]["n"])[:12])
    print(json.dumps({
        "period_days": a.days, "student": stu,
        "new_in_period": len(recent(pool)),
        "reviewed_in_period": len(rp),
        "corrections_in_period": corrections,
        "cleared_pass_in_period": cleared,
        "escalated_total": states.get("escalated", 0),
        "hold_pending": len([w for w in pool if w.get("state") == "hold"]),
        "open_band_cost_by_subject": open_cost,
        "weak_clusters": top,
        "by_subject": bysub, "x_axis_dist": byx, "state_dist": states,
        "exam_mode_cfg": c.get("exam_mode"), "targets": c.get("targets"),
        "policy_year": c.get("policy_year"),
        "note": "报告数字以此JSON为准（流水已按卡池join过滤:学生+demo+advance；成本每卡计一次）"},
        ensure_ascii=False, indent=1))

def cmd_kp_candidates(a):
    seen = {}
    for w in load("wrong"):
        for k in w.get("kp_ids", []):
            if str(k).startswith("grow:"): seen[k] = seen.get(k, 0) + 1
    print(json.dumps(seen, ensure_ascii=False, indent=1))

def cmd_status(a):
    c = cfg()
    pool = [w for w in load("wrong") if w.get("id")]
    due = _plan_recs(c)
    print(json.dumps({"home": str(HOME),
                      "questions": len(load("questions")),
                      "wrong": len(pool), "variants": len(load("variants")),
                      "due_today": len(due),
                      "hold": len([w for w in pool if w.get("state") == "hold"]),
                      "escalated": len([w for w in pool if w.get("state") == "escalated"]),
                      "no_state_skipped": len([w for w in pool if not w.get("state")])},
                     ensure_ascii=False))

def cmd_migrate(a):
    """并树迁移：grow:候选 → 正式知识块ID，questions+wrong-answers 两处同步改写。
    先在本表对应 subjects/*.md 加正式行（编号规则：科前缀+下一空号），再执行本命令。"""
    qs, wr = load("questions"), load("wrong")
    nq = nw = 0
    for r in qs:
        ks = r.get("kp_ids", [])
        if a.from_ in ks:
            r["kp_ids"] = [a.to if k == a.from_ else k for k in ks]; nq += 1
    for r in wr:
        ks = r.get("kp_ids", [])
        if a.from_ in ks:
            r["kp_ids"] = [a.to if k == a.from_ else k for k in ks]; nw += 1
    rewrite("questions", qs); rewrite("wrong", wr)
    print(json.dumps({"from": a.from_, "to": a.to,
                      "questions_updated": nq, "wrong_updated": nw,
                      "reminder": "确认已在 subjects 表新增该正式ID行"}, ensure_ascii=False))

def cmd_amend(a):
    """修订已入库卡片字段（识别转写更正专用；限当前学生，走 _find 隔离）。"""
    _find(a.id) or fail({"error": "not found for current student"})
    patch = json.loads(Path(a.file).read_text(encoding="utf-8"))
    # 同步派生字段：wrong.score_axis 变更时顶层快照一并更新（band_points/stats/clusters 读顶层）
    sa = patch.get("wrong", {}).get("score_axis")
    if sa: patch["score_axis"] = sa
    _upd_wid(a.id, patch)
    print(json.dumps({"amended": a.id, "fields": list(patch)}, ensure_ascii=False))

def cmd_exam_init(a):
    """登记一张试卷（整卷识别的题面骨架），后续错题卡带 exam_paper_id+qno 关联。"""
    e = json.loads(Path(a.file).read_text(encoding="utf-8"))
    if not e.get("id"): e["id"] = "ex-" + uuid.uuid4().hex[:10]
    e["created_at"] = now()
    exs = load("exams")
    if any(x["id"] == e["id"] for x in exs):
        fail({"error": "exam id exists"}); return
    append("exams", e)
    print(json.dumps({"exam": e["id"], "name": e.get("name"),
                      "questions": len(e.get("questions", []))}, ensure_ascii=False))

def cmd_exam_summary(a):
    """整卷报告数据：每题 对错/得分/丢分/关联错题卡（数字全部由记录派生）。"""
    c = cfg(); stu = c.get("student", "default")
    e = next((x for x in load("exams") if x["id"] == a.id), None)
    if not e: fail({"error": "exam not found"})
    cards = {w.get("qno"): w for w in load("wrong")
             if w.get("exam_paper_id") == a.id and w.get("student", "default") == stu}
    rows, lost = [], 0.0
    for q in e.get("questions", []):
        w = cards.get(q["no"])
        got = q.get("points", 0) if w is None else (w.get("points_got") or 0)
        d = q.get("points", 0) - got
        lost += max(d, 0)
        rows.append({"no": q["no"], "points_max": q.get("points"), "points_got": got,
                     "lost": round(max(d, 0), 1), "correct": w is None,
                     "card": (w or {}).get("id"), "kp": (w or {}).get("kp_ids", []),
                     "x": (w or {}).get("wrong", {}).get("x_primary", "")})
    print(json.dumps({"exam": e["id"], "name": e.get("name"), "date": e.get("date"),
                      "subject": e.get("subject"), "total": e.get("total"),
                      "score": round(sum(r["points_got"] for r in rows), 1),
                      "unattributed_lost": round(lost, 1), "per_question": rows,
                      "wrong_count": len([r for r in rows if not r["correct"]]),
                      "note": "correct=true 表示无错题卡关联；得分以 points_got 为准"},
                      ensure_ascii=False, indent=1))

def cmd_clusters(a):
    """考点簇定期总结数据：近N天开放错题按 学科·知识块 聚类，含丢分/错因/断点样本。"""
    c = cfg(); stu = c.get("student", "default")
    since = today() - timedelta(days=a.days)
    cl = {}
    for w in load("wrong"):
        if w.get("student", "default") != stu or w.get("origin") in ("demo", "watch"): continue
        if w.get("state") in ("mastered", "archived"): continue
        if (dparse(w.get("created_at")) or today()) < since: continue
        for k in w.get("kp_ids", []):
            key = f"{w.get('subject','?')}·{k}"
            e = cl.setdefault(key, {"n": 0, "lost": 0.0, "ids": [], "x": {},
                                    "breakpoints": []})
            e["n"] += 1; e["ids"].append(w["id"])
            e["lost"] = round(e["lost"] + band_points(w), 1)
            xp = w.get("wrong", {}).get("x_primary", "?"); e["x"][xp] = e["x"].get(xp, 0) + 1
            bp = w.get("wrong", {}).get("breakpoint", "")
            if bp: e["breakpoints"].append(bp[:60])
    top = sorted(cl.items(), key=lambda kv: (-kv[1]["lost"], -kv[1]["n"]))[:a.top]
    out = {k: v for k, v in top if v["n"] >= a.min}
    print(json.dumps({"period_days": a.days, "clusters": out,
                      "usage": "对入选簇逐个出：簇深潜（共性错因→考点精讲→3道变式）；建议每周跑一次并配 cron"},
                      ensure_ascii=False, indent=1))

def cmd_gate_record(a):
    data = json.loads(Path(a.file).read_text(encoding="utf-8"))
    items = data if isinstance(data, list) else [data]
    for rec in items:
        append("log", {"kind": "gate", "at": now(), **rec})
    print(json.dumps({"recorded": len(items)}, ensure_ascii=False))

VERDICT_OK = ("PASS", "WARNING-CONFIRMED")   # 字面令牌定义见 references/review-gate.md
def cmd_variant_import(a):
    items = json.loads(Path(a.file).read_text(encoding="utf-8"))
    items = items if isinstance(items, list) else [items]
    n, rej = 0, []
    for v in items:
        if v.get("verdict") not in VERDICT_OK:
            rej.append({"local_id": v.get("local_id", "?"),
                        "verdict": v.get("verdict"),
                        "reason": "verdict 必须字面为 PASS 或 WARNING-CONFIRMED"}); continue
        v.setdefault("imported_at", now()); append("variants", v); n += 1
    print(json.dumps({"imported": n, "rejected": rej}, ensure_ascii=False))

def main():
    p = argparse.ArgumentParser(prog="cuoti.py", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").set_defaults(fn=cmd_init)
    g = sub.add_parser("set"); g.add_argument("pairs", nargs="+"); g.set_defaults(fn=cmd_set)
    d = sub.add_parser("dedup"); d.add_argument("--image"); d.add_argument("--text")
    d.add_argument("--student"); d.set_defaults(fn=cmd_dedup)
    a1 = sub.add_parser("add"); a1.add_argument("--file", required=True)
    a1.add_argument("--force", action="store_true", help="跳过查重（用户明确要求复录）")
    a1.set_defaults(fn=cmd_add)
    a2 = sub.add_parser("confirm"); a2.add_argument("--id", required=True)
    a2.set_defaults(fn=cmd_confirm)
    a3 = sub.add_parser("grade"); a3.add_argument("--id", required=True)
    a3.add_argument("--result", required=True, choices=["pass", "fail",
                  "pending_confirmation", "browse", "wake_pass"])
    a3.add_argument("--keypoints", default="ok", choices=["ok", "partial", "bad"])
    a3.add_argument("--basis", default=""); a3.set_defaults(fn=cmd_grade)
    a4 = sub.add_parser("plan"); a4.add_argument("--subject")
    a4.add_argument("--exam-mode", action="store_true"); a4.add_argument("--limit", type=int, default=8)
    a4.set_defaults(fn=cmd_plan)
    a5 = sub.add_parser("similar"); a5.add_argument("--id", required=True)
    a5.add_argument("--top", type=int, default=6); a5.set_defaults(fn=cmd_similar)
    a6 = sub.add_parser("stats"); a6.add_argument("--days", type=int, default=7)
    a6.set_defaults(fn=cmd_stats)
    sub.add_parser("kp-candidates").set_defaults(fn=cmd_kp_candidates)
    mg = sub.add_parser("migrate"); mg.add_argument("--from", dest="from_", required=True)
    mg.add_argument("--to", required=True); mg.set_defaults(fn=cmd_migrate)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    g2 = sub.add_parser("gate-record"); g2.add_argument("--file", required=True)
    g2.set_defaults(fn=cmd_gate_record)
    ei = sub.add_parser("exam-init"); ei.add_argument("--file", required=True)
    ei.set_defaults(fn=cmd_exam_init)
    es = sub.add_parser("exam-summary"); es.add_argument("--id", required=True)
    es.set_defaults(fn=cmd_exam_summary)
    cc = sub.add_parser("clusters"); cc.add_argument("--days", type=int, default=30)
    cc.add_argument("--top", type=int, default=6); cc.add_argument("--min", type=int, default=2)
    cc.set_defaults(fn=cmd_clusters)
    am = sub.add_parser("amend"); am.add_argument("--id", required=True)
    am.add_argument("--file", required=True); am.set_defaults(fn=cmd_amend)
    v = sub.add_parser("variant-import"); v.add_argument("--file", required=True)
    v.set_defaults(fn=cmd_variant_import)
    a = p.parse_args(); a.fn(a)

if __name__ == "__main__":
    main()
