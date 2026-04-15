<script setup lang="ts">
import { useSessionStore } from "../stores/session";

const sessionStore = useSessionStore();
</script>

<template>
  <section class="view-page view-tools-page">
    <div class="page-head wide">
       <h2>Skills 知识增强与测试工具</h2>
      <div class="page-head-actions">
       <button class="secondary-btn"><i class="fa-brands fa-github"></i> Github 安装</button>
        <button class="primary-btn"><i class="fa-solid fa-upload"></i> 上传 .md 技能包</button>
      </div>
    </div>

     <h3 class="section-title">已安装 Skills (程序化指导知识)</h3>
    <p class="head-desc long">Skills 是 Markdown 格式的指令集，执行时将作为“便签”注入到 LLM 系统提示词中，增强 Agent 的专业能力。</p>

    <div class="skills-grid">
      <article class="skill-card">
        <div class="skill-icon"><i class="fa-brands fa-markdown"></i></div>
        <div class="skill-body">
          <div class="skill-head">
            <h4>webapp-testing-guidelines</h4>
           <span class="skill-state enabled">已启用</span>
          </div>
           <p>提供 Web 表单、弹窗交互、瞬态 UI 处理的标准测试规范。</p>
          <div class="mono muted-small">Source: anthropics/webapp-testing</div>
        </div>
      </article>

      <article class="skill-card dimmed">
        <div class="skill-icon"><i class="fa-brands fa-markdown"></i></div>
        <div class="skill-body">
          <div class="skill-head">
            <h4>api-security-fuzzing</h4>
           <span class="skill-state disabled">已禁用</span>
          </div>
           <p>提供 API 安全测试的负载生成指导，包括认证绕过、SQLi 和 XSS 样式探针。</p>
           <div class="mono muted-small">Source: 本地上传 (MinIO)</div>
        </div>
      </article>
    </div>

     <h3 class="section-title">安全与系统探测器 (Scanners)</h3>
    <div class="scanner-grid">
      <article class="scanner-card">
        <i class="fa-solid fa-satellite-dish"></i>
         <span>Nuclei 引擎</span>
        <small>基于模板的快速漏洞扫描</small>
      </article>
      <article class="scanner-card">
        <i class="fa-solid fa-database"></i>
       <span>SQLMap 注入</span>
        <small>自动化 SQL 注入与探测</small>
      </article>
      <article class="scanner-card">
        <i class="fa-solid fa-bug"></i>
         <span>XSStrike 探测</span>
        <small>高级 XSS 跨站模糊测试</small>
      </article>
      <article class="scanner-card">
        <i class="fa-solid fa-bolt"></i>
       <span>定制 Fuzzing</span>
        <small>自动组装与注入 Payload</small>
      </article>
    </div>

    <div class="registry-strip" v-if="sessionStore.agents.length">
       <div class="registry-strip-title">当前后端已注册 Agent / Tool</div>
      <div class="registry-tags">
        <span v-for="agent in sessionStore.agents" :key="agent.key" class="registry-tag">{{ agent.name }}</span>
        <span v-for="tool in sessionStore.tools" :key="tool.key" class="registry-tag light">{{ tool.name }}</span>
      </div>
    </div>
  </section>
</template>
