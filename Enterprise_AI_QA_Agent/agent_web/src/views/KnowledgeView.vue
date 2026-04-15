<template>
  <section class="view-page">
    <div class="page-head">
      <div>
        <h2>页面知识库 (RAG Memory)</h2>
        <p class="head-desc">基于 Qdrant 向量数据库，存储页面结构与 Diff 变更引擎</p>
      </div>
      <div class="page-head-actions">
       <button class="secondary-btn"><i class="fa-solid fa-plus"></i> 手动录入知识</button>
        <button class="primary-btn"><i class="fa-solid fa-rotate-right"></i> 刷新同步状态</button>
      </div>
    </div>

    <div class="stats-grid">
      <div class="stat-card">
        <div>
          <div class="stat-label">总记录数 (MySQL)</div>
          <div class="stat-value">1,420</div>
        </div>
        <div class="stat-icon"><i class="fa-solid fa-database"></i></div>
      </div>
      <div class="stat-card">
        <div>
          <div class="stat-label">已向量化 (Qdrant)</div>
          <div class="stat-value">1,420</div>
        </div>
        <div class="stat-icon"><i class="fa-solid fa-layer-group"></i></div>
      </div>
      <div class="stat-card">
        <div>
          <div class="stat-label">老化待更新 (Stale)</div>
          <div class="stat-value danger-text">12</div>
        </div>
        <div class="stat-icon red"><i class="fa-solid fa-triangle-exclamation"></i></div>
      </div>
      <div class="stat-card">
        <div>
          <div class="stat-label">健康状态</div>
          <div class="stat-status">Healthy</div>
        </div>
        <div class="stat-icon green"><i class="fa-solid fa-heart-pulse"></i></div>
      </div>
    </div>

    <div class="knowledge-grid">
      <article class="knowledge-card">
        <div class="knowledge-badge">已同步 (Synced)</div>
        <h3><i class="fa-solid fa-globe"></i> /login</h3>
        <p class="mono head-desc">https://staging.app.com/login</p>
        <div class="knowledge-meta">
          <div><span>识别 UI 元素:</span><strong>24 个</strong></div>
          <div><span>关联测试用例:</span><strong>8 条</strong></div>
        </div>
        <div class="knowledge-foot">
          <span class="muted"><i class="fa-regular fa-clock"></i> 更新于 2 小时前</span>
          <div class="icon-row">
            <button class="icon-btn"><i class="fa-solid fa-eye"></i></button>
            <button class="icon-btn danger"><i class="fa-solid fa-trash"></i></button>
          </div>
        </div>
      </article>

      <article class="knowledge-card stale">
        <div class="knowledge-badge stale-badge">老化 (Stale)</div>
        <h3><i class="fa-solid fa-globe"></i> /dashboard/settings</h3>
        <p class="mono head-desc">https://staging.app.com/dashboard/settings</p>
        <div class="warning-box">
          <i class="fa-solid fa-triangle-exclamation"></i>
          页面结构发生变更，建议在下次自动运行前进行重新发现。
        </div>
        <button class="danger-btn full-btn">触发重新发现 (Rediscovery)</button>
      </article>
    </div>
  </section>
</template>
