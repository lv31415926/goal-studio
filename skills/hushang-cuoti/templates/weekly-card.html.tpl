<!doctype html>
<!-- 周报/今日清单长图模板（doc_render_longimage 渲染，宽1080）。
     填充规则：{{...}} 由 cuoti.py stats/plan 的 JSON 输出注入，禁止手工填数。
     配色基线：深蓝金（--accent 可换浅墨白：#1c2430/#e8e2d5）。 -->
<html><head><meta charset="utf-8"><style>
 body{margin:0;width:1080px;font-family:"PingFang SC","Microsoft YaHei",sans-serif;
      background:#0b1c33;color:#eef3fa}
 .wrap{padding:36px 44px}
 header{border-bottom:3px solid #c9a45c;padding-bottom:18px;margin-bottom:24px}
 h1{font-size:44px;margin:0;color:#f5d99a}
 .sub{font-size:24px;color:#9fb3cc;margin-top:8px}
 .count{font-size:26px;color:#ffd27a;margin-top:6px}
 .stats{display:flex;gap:18px;margin:22px 0}
 .stat{flex:1;background:#12294a;border-radius:14px;padding:18px;text-align:center}
 .stat b{display:block;font-size:40px;color:#f5d99a}
 .stat span{font-size:20px;color:#9fb3cc}
 .item{background:#12294a;border-left:6px solid #c9a45c;border-radius:10px;
       padding:16px 20px;margin:12px 0;font-size:26px;line-height:1.5}
 .item.escalated{border-left-color:#e05555}
 .tag{display:inline-block;background:#1d3d68;border-radius:8px;padding:2px 12px;
      font-size:20px;margin-right:8px;color:#cfe2f7}
 .band{color:#ffd27a;font-size:20px}
 .weak{background:#16304f;border-radius:12px;padding:18px 22px;margin:10px 0;font-size:24px}
 footer{margin-top:28px;font-size:20px;color:#7a93b3;border-top:1px solid #1d3d68;padding-top:12px}
</style></head><body><div class="wrap">
 <header><h1>沪上错题 · {{report_type}}</h1>
  <div class="sub">{{student_name}} ｜ {{grade}} ｜ {{date}}</div>
  <div class="count">距{{next_exam_label}} {{countdown_days}} 天（预估口径{{policy_year}}）</div></header>
 <div class="stats">
  <div class="stat"><b>{{stat_new}}</b><span>本期收录</span></div>
  <div class="stat"><b>{{stat_reviewed}}</b><span>完成复习</span></div>
  <div class="stat"><b>{{stat_cleared}}</b><span>成功销号</span></div>
  <div class="stat"><b>{{stat_escalated}}</b><span>重点追踪</span></div>
 </div>
 <h2 style="font-size:30px;color:#cfe2f7">今日复习清单</h2>
 {{plan_items_html}}
 <!-- 每条 .item 示例：
 <div class="item"><span class="tag">物理</span><span class="tag">P19电磁感应</span>
   wa-a1b2c3d4e5f6 单杆切割图像题 <span class="band">≈掉半级/1.5分</span>
   <div style="font-size:20px;color:#9fb3cc">断点：第3步拐错弯——没先判回路方向 ｜ 遮答案重做</div></div> -->
 <h2 style="font-size:30px;color:#cfe2f7">薄弱簇 Top{{weak_n}}</h2>
 {{weak_clusters_html}}
 <h2 style="font-size:30px;color:#cfe2f7">下一步（最多3条）</h2>
 {{actions_html}}
 <footer>数据全部来自本地错题本 cuoti.py stats（{{generated_at}}）；政策口径 {{policy_year}}，
 等第换算为经验值仅供参考；本卡片含学生数据，转发前确认收件人。</footer>
</div></body></html>
