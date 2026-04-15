<template>
  <section class="view-page">
    <div class="page-head">
      <h2>任务池与执行列队 (Task Pool)</h2>
      <div class="page-head-actions">
        <div class="search-box">
          <i class="fa-solid fa-search"></i>
          <input type="text" placeholder="搜索用例ID或名称..." />
        </div>
        <button class="secondary-btn"><i class="fa-solid fa-stop"></i> 全部停止</button>
        <button class="primary-btn"><i class="fa-solid fa-play"></i> 运行选中</button>
      </div>
    </div>

    <div class="table-shell">
      <table class="prototype-table">
        <thead>
          <tr>
            <th><input type="checkbox" /></th>
            <th>用例 ID</th>
            <th>任务名称 (Title)</th>
            <th>测试类型</th>
            <th>当前状态</th>
            <th>Agent / 模型</th>
            <th class="align-right">操作</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><input type="checkbox" /></td>
            <td class="mono strong">CASE-1024</td>
            <td class="strong">验证标准登录流程 (正常账号)</td>
            <td><span class="pill gray">单量执行</span></td>
            <td><span class="pill blue"><span class="small-dot pulse-dot blue"></span> 浏览器操作中</span></td>
            <td class="mono">DeepSeek-V3</td>
            <td class="align-right"><button class="icon-btn danger"><i class="fa-solid fa-stop"></i></button></td>
          </tr>
          <tr>
            <td><input type="checkbox" /></td>
            <td class="mono strong">CASE-1025</td>
            <td class="strong">登录异常输入边界 Fuzzing</td>
            <td><span class="pill gray">批量执行</span></td>
            <td><span class="pill neutral"><i class="fa-regular fa-clock"></i> 排队中</span></td>
            <td class="mono">-</td>
            <td class="align-right"><button class="icon-btn"><i class="fa-solid fa-ellipsis-vertical"></i></button></td>
          </tr>
          <tr>
            <td><input type="checkbox" /></td>
            <td class="mono strong">API-902</td>
            <td class="strong">越权漏洞探测 (/api/admin/users)</td>
            <td><span class="pill gray">接口安全测试</span></td>
            <td><span class="pill green"><i class="fa-solid fa-check"></i> 已完成</span></td>
            <td class="mono">GPT-4o</td>
            <td class="align-right"><button class="icon-btn"><i class="fa-solid fa-ellipsis-vertical"></i></button></td>
          </tr>
          <tr>
            <td><input type="checkbox" /></td>
            <td class="mono strong">CASE-1021</td>
            <td class="strong">密码重置流程测试</td>
            <td><span class="pill gray">单量执行</span></td>
            <td><span class="pill red"><i class="fa-solid fa-xmark"></i> 验证失败</span></td>
            <td class="mono">DeepSeek-V3</td>
            <td class="align-right"><button class="icon-btn"><i class="fa-solid fa-ellipsis-vertical"></i></button></td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>
