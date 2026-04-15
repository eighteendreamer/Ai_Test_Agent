<template>
  <section class="view-page report-page">
    <div class="page-head">
      <div>
        <h2>综合评估报告 (Execution Reports)</h2>
        <p class="head-desc">多报告聚合分析，质量评级与改进建议展示</p>
      </div>
      <button class="primary-btn"><i class="fa-regular fa-paper-plane"></i> 发送报告邮件</button>
    </div>

    <div class="report-layout">
      <div class="report-list">
        <h3>最近运行批次 (Batches)</h3>

        <article class="report-batch active">
          <div class="report-batch-head">
            <span>#Run-20260311-A</span>
            <span class="grade green">评级 A</span>
          </div>
          <div class="muted-small mono">包含 12 个功能用例, 1 个安全扫描</div>
          <div class="mini-tags">
            <span>Passed: 11</span>
            <span class="danger-soft">Failed: 1</span>
          </div>
        </article>

        <article class="report-batch">
          <div class="report-batch-head">
            <span>#Run-20260310-B</span>
            <span class="grade red">评级 C</span>
          </div>
          <div class="muted-small mono">包含 45 个全回归用例</div>
          <div class="mini-tags">
            <span>Passed: 38</span>
            <span class="danger-soft">Failed: 7</span>
          </div>
        </article>
      </div>

      <div class="report-detail">
        <div class="report-summary-head">
          <div>
            <h3>批次 #Run-20260311-A 分析结果</h3>
            <div class="head-desc"><i class="fa-solid fa-robot"></i> AI 聚合诊断已完成，耗时 4m 12s</div>
          </div>
          <div class="grade-box">A</div>
        </div>

         <h4 class="section-title small">异常用例剖析 (Element Mismatch)</h4>
        <div class="error-analysis">
          <div class="error-analysis-head">
            <span>点击 '忘记密码' 链接 (CASE-1021)</span>
            <button class="secondary-btn red-outline">推送至禅道 (Zentao)</button>
          </div>
          <p class="error-analysis-text">
               Agent 尝试基于历史知识库定位 '忘记密码' 链接，但目标页面的 DOM 结构已发生变更。执行中断以防误操作。
          </p>
          <div class="diff-grid">
            <div class="diff-card old">
              <div class="new-badge">知识库参考 (旧)</div>
              <div class="diff-card-head">Text: "Forgot Password?"</div>
            </div>
            <div class="diff-card fresh">
              <div class="new-badge">新发现</div>
              <div class="diff-card-head">实时 DOM 抓取 (新)</div>
              <div class="mono">Selector: a.text-sm.reset-link<br />Text: "Reset password"</div>
            </div>
          </div>
        </div>

        <h4 class="section-title small">AI 修复建议 (Suggestions)</h4>
        <div class="suggestion-box">
          <ul>
             <li>使用新的选择器 <code>a.text-sm.reset-link</code> 更新知识库向量 #4928。</li>
            <li>系统 API `/api/admin/users` 安全扫描未发现越权，但检测到响应头缺少 `Strict-Transport-Security`，建议补充基线配置。</li>
          </ul>
        </div>
      </div>
    </div>
  </section>
</template>
