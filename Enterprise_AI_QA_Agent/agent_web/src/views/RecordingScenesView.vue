<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { api } from "../services/api";
import { HttpError } from "../services/http";
import { scenesApi, type SceneBundle, type SceneContent, type SceneEdit, type SceneSummary } from "../features/recorder/sceneApi";
import { listRecordings, type RecordingPublic } from "../features/recorder/recordingApi";

const route = useRoute();
const projectId = computed(() => String(route.params.projectId || ""));
const projectName = ref("");
const archived = ref(false);
const scenes = ref<SceneSummary[]>([]);
const recordings = ref<RecordingPublic[]>([]);
const recordingId = ref("");
const selected = ref<SceneBundle | null>(null);
const content = ref<SceneContent | null>(null);
const history = ref<Array<{id: string; version: number}>>([]);
const reason = ref("");
const error = ref("");
const message = ref("");
const busy = ref(false);
const offset = ref(0);
const hasMore = ref(false);
const assertionsText = ref("");
const storageWarning = ref("");
const pending = ref<{projectId: string; sceneId?: string; versionRequestId?: string; creation?: {request_id: string; recording_id: string}; edit?: SceneEdit} | null>(null);
let revision = 0;
let listRevision = 0;
const readOnly = computed(() => archived.value || selected.value?.current_version_id !== selected.value?.version.id || !!pending.value);
const storageKey = () => `recording-scene-submit:${projectId.value}`;
function persistPending() {
  try {
    if (pending.value) window.sessionStorage.setItem(storageKey(),JSON.stringify({projectId:pending.value.projectId,
      sceneId:pending.value.sceneId,versionRequestId:pending.value.edit?.request_id || pending.value.versionRequestId,
      creation:pending.value.creation}));
    else window.sessionStorage.removeItem(storageKey());
    storageWarning.value="";
  } catch { storageWarning.value="浏览器未能保留提交身份，请保存下方请求编号，重新打开页面后先核对结果。"; }
}
function restorePending() {
  try {
    const raw=window.sessionStorage.getItem(storageKey());
    if (!raw) return;
    const saved=JSON.parse(raw);
    const validId=(value: unknown)=>typeof value==="string" && !!value && value.length<=160;
    if (saved.projectId!==projectId.value || (!saved.creation && !saved.versionRequestId)
      || (saved.creation && (!validId(saved.creation.request_id)||!validId(saved.creation.recording_id)))
      || (saved.versionRequestId && (!validId(saved.versionRequestId)||!validId(saved.sceneId)))) throw new Error("invalid saved identity");
    pending.value={projectId:saved.projectId,sceneId:saved.sceneId,creation:saved.creation,versionRequestId:saved.versionRequestId};
  } catch { storageWarning.value="无法读取旧提交身份，请先核对场景版本，不要重复创建。"; }
}

function useBundle(bundle: SceneBundle) {
  selected.value = bundle;
  content.value = JSON.parse(JSON.stringify(bundle.version.content)) as SceneContent;
  assertionsText.value = JSON.stringify(content.value.assertions, null, 2);
  reason.value = "";
}
async function refreshList() {
  const id = projectId.value;
  const current = revision;
  const listCurrent = ++listRevision;
  try {
    const page = await scenesApi.list(id, offset.value);
    if (current !== revision || listCurrent !== listRevision) return;
    scenes.value = page.items;
    hasMore.value = page.has_more;
  } catch (e) { if (current===revision && listCurrent===listRevision) error.value=e instanceof Error ? e.message : String(e); }
}
async function load() {
  const current = ++revision;
  selected.value = null; content.value = null; pending.value = null;
  error.value = ""; message.value = ""; offset.value = 0; scenes.value = []; recordings.value = [];
  storageWarning.value=""; restorePending();
  busy.value = true;
  const id = projectId.value;
  try {
    const [overview, items, list] = await Promise.all([api.getProjectOverview(id), listRecordings(200, {projectId:id}), scenesApi.list(id)]);
    if (current !== revision) return;
    projectName.value = overview.project.name; archived.value = overview.project.status === "archived";
    recordings.value = items.filter(r => r.status === "completed"); recordingId.value = recordings.value[0]?.id || "";
    scenes.value = list.items; hasMore.value = list.has_more;
  } catch (e) { if (current === revision) error.value = e instanceof Error ? e.message : String(e); }
  finally { if (current === revision) busy.value = false; }
}
async function select(sceneId: string, versionId?: string) {
  const id = projectId.value, current = revision;
  busy.value = true; error.value = ""; message.value = "";
  try {
    const [bundle, versions] = await Promise.all([scenesApi.get(id,sceneId,versionId),scenesApi.versions(id,sceneId)]);
    if (current !== revision) return;
    useBundle(bundle); history.value = versions;
  } catch (e) { if (current === revision) error.value = e instanceof Error ? e.message : String(e); }
  finally { if (current === revision) busy.value = false; }
}
async function submit(create: boolean) {
  const id = projectId.value, current = revision;
  error.value = ""; message.value = "";
  try {
    if (!pending.value) {
      if (create) pending.value = {projectId:id,creation:{request_id:crypto.randomUUID(),recording_id:recordingId.value}};
      else if (content.value && selected.value) {
        const assertions: unknown = JSON.parse(assertionsText.value);
        if (!Array.isArray(assertions)) throw new Error("断言需要 JSON 数组");
        const payload = JSON.parse(JSON.stringify(content.value)) as SceneContent;
        payload.assertions = assertions as SceneContent["assertions"];
        pending.value = {projectId:id,sceneId:selected.value.scene_id,edit:{request_id:crypto.randomUUID(),parent_version_id:selected.value.version.id,change_reason:reason.value,content:payload}};
      }
    }
    const request = pending.value;
    if (!request || request.projectId !== id) return;
    if (!request.creation && !request.edit) throw new Error("原编辑内容未保存在浏览器，请先查询原提交结果");
    persistPending();
    busy.value = true;
    const bundle = request.creation ? await scenesApi.create(id,request.creation) : await scenesApi.save(id,request.sceneId!,request.edit!);
    if (current !== revision) return;
    pending.value = null; persistPending(); await refreshList(); await select(bundle.scene_id,bundle.version.id);
    message.value = `已保存 V${bundle.version.version}`;
  } catch (e) { if (current === revision) {
    if (e instanceof HttpError && [400,404,409,422].includes(e.status)) { pending.value=null; persistPending(); }
    error.value = e instanceof Error ? e.message : String(e);
  } }
  finally { if (current === revision) busy.value = false; }
}
async function recover() {
  const request = pending.value, current = revision;
  if (!request) return;
  busy.value = true; error.value = "";
  try {
    const bundle = request.creation ? await scenesApi.recoverCreation(request.projectId,request.creation.request_id)
      : await scenesApi.recoverVersion(request.projectId,request.sceneId!,request.edit?.request_id || request.versionRequestId!);
    if (current !== revision) return;
    if (!bundle) { message.value = "尚未查询到提交。可使用原请求身份重试。"; return; }
    pending.value = null; persistPending(); await refreshList(); await select(bundle.scene_id,bundle.version.id); message.value = "已恢复保存结果";
  } catch (e) { if (current === revision) error.value = e instanceof Error ? e.message : String(e); }
  finally { if (current === revision) busy.value = false; }
}
function move(index: number, delta: number) {
  const steps = content.value?.steps;
  if (!steps || index+delta < 0 || index+delta >= steps.length) return;
  [steps[index],steps[index+delta]] = [steps[index+delta]!,steps[index]!];
}
async function draft(read=false) {
  if (!selected.value) return;
  const bundle = selected.value, current=revision;
  busy.value=true; error.value="";
  try {
    const saved=await scenesApi.draft(projectId.value,bundle.scene_id,bundle.version.id,read);
    if (current!==revision) return;
    message.value=saved ? `草稿已保存：${saved.case.case_key}（需评审启用）` : "该版本尚无草稿";
  } catch(e) { if(current===revision) error.value=e instanceof Error ? e.message : String(e); }
  finally { if(current===revision) busy.value=false; }
}
watch(projectId, load, {immediate:true});
onBeforeUnmount(() => { revision++; listRevision++; });
</script>

<template>
  <section class="view-page recording-scenes">
    <header><div><h2>录制场景 · {{ projectName }}</h2><p>整理操作、预期和元素别名；每次保存保留历史版本，整理状态不代表测试通过。</p></div><RouterLink :to="`/projects/${projectId}`">返回项目</RouterLink></header>
    <p v-if="error" role="alert" class="error">{{ error }}</p><p v-if="message" role="status">{{ message }}</p>
    <p v-if="storageWarning" class="error">{{ storageWarning }}</p>
    <div v-if="pending" class="pending"><span>提交身份 {{ pending.creation?.request_id || pending.edit?.request_id || pending.versionRequestId }} 已保留，请先查询保存结果。</span><button :disabled="busy" @click="recover">恢复提交</button><button :disabled="busy || (!pending.creation && !pending.edit)" @click="submit(!!pending.creation)">使用原身份重试</button></div>
    <div class="create"><label>已完成录制<select v-model="recordingId" :disabled="busy || !!pending"><option v-for="r in recordings" :key="r.id" :value="r.id">{{ r.name || r.id }}</option></select></label><button :disabled="busy || archived || !recordingId || !!pending" @click="submit(true)">从录制创建场景</button><small>显示最近 200 份录制</small></div>
    <div class="scene-layout"><aside><button v-for="s in scenes" :key="s.scene_id" :disabled="busy || !!pending" :class="{active:s.scene_id===selected?.scene_id}" @click="select(s.scene_id)">{{ s.name }} · V{{ s.version }} · {{ s.step_count }} 步</button><p v-if="!scenes.length">暂无场景</p><button :disabled="busy || offset===0 || !!pending" @click="offset=Math.max(0,offset-50); refreshList()">上一页</button><button :disabled="busy || !hasMore || !!pending" @click="offset+=50; refreshList()">下一页</button></aside>
    <main v-if="selected && content">
      <label>历史版本<select aria-label="历史版本" :value="selected.version.id" :disabled="busy || !!pending" @change="select(selected.scene_id,($event.target as HTMLSelectElement).value)"><option v-for="v in history" :key="v.id" :value="v.id">V{{ v.version }}</option></select></label>
      <p v-if="readOnly && !pending">历史版本或归档项目只读</p>
      <fieldset :disabled="busy || readOnly"><label>场景名称<input v-model="content.name" maxlength="240"></label><label>场景说明<textarea v-model="content.description" /></label><label>整理状态<select v-model="content.status"><option value="draft">草稿</option><option value="organized">已整理</option><option value="archived">归档</option></select></label>
      <h3>步骤整理</h3><div v-for="(step,index) in content.steps" :key="index" class="scene-step"><span>步骤 {{ index+1 }} · 原事件 #{{ step.source_seq }}</span><label>操作说明<input v-model="step.action" /></label><label>预期结果<textarea v-model="step.expected" /></label><label>元素别名<select aria-label="元素别名" v-model="step.alias"><option :value="null">使用原定位</option><option v-for="a in content.aliases" :key="a.name" :value="a.name">{{ a.name }}</option></select></label><button @click="move(index,-1)">上移</button><button @click="move(index,1)">下移</button><button :disabled="content.steps.length===1" @click="content.steps.splice(index,1)">从场景移除</button></div>
      <h3>元素业务别名</h3><p>引用原事件的元素与定位证据；不改写选择器。</p><div v-for="(alias,index) in content.aliases" :key="index" class="scene-alias"><label>别名<input v-model="alias.name"></label><label>原动作序号<input v-model.number="alias.source_seq" type="number"></label><label>说明<input v-model="alias.description"></label><button @click="content.aliases.splice(index,1)">移除别名</button></div><button @click="content.aliases.push({name:'',source_seq:content.steps[0]!.source_seq,description:''})">添加别名</button>
      <label>业务断言 JSON<textarea v-model="assertionsText" rows="7" /></label><label>执行说明<textarea v-model="content.execution_notes" rows="4" /></label><label>本次修改原因<input v-model="reason"></label><button :disabled="!reason.trim()" @click="submit(false)">保存新版本</button></fieldset>
      <div class="draft-actions"><button :disabled="busy || archived || !!pending || content.status==='archived'" @click="draft()">将已保存版本转为用例草稿</button><button :disabled="busy" @click="draft(true)">查询已保存草稿</button><RouterLink :to="`/projects/${projectId}`">查看项目用例</RouterLink></div>
      <details><summary>固定来源与完整事件</summary><pre>{{ JSON.stringify(selected.version.source,null,2) }}</pre></details>
    </main></div>
  </section>
</template>

<style scoped>
.recording-scenes{padding:24px;color:var(--text-primary)}header,.create,.draft-actions,.pending{display:flex;gap:16px;align-items:center;justify-content:space-between;margin-bottom:20px}h2,h3,p{margin:8px 0}label{display:flex;flex-direction:column;gap:6px;margin:12px 0}input,textarea,select{padding:9px;border:1px solid var(--border-color,#cbd5e1);border-radius:7px;background:var(--bg-primary,#fff);color:inherit;width:100%;box-sizing:border-box}button{padding:9px 12px;margin:4px;border:1px solid var(--border-color,#cbd5e1);border-radius:7px;background:var(--bg-secondary,#f1f5f9);color:inherit;cursor:pointer}button:disabled{opacity:.5;cursor:default}.scene-layout{display:grid;grid-template-columns:280px minmax(0,1fr);gap:24px}aside button{display:block;width:100%;text-align:left}.active{outline:2px solid #3b82f6}.scene-step,.scene-alias{border:1px solid var(--border-color,#cbd5e1);padding:12px;border-radius:10px;margin:12px 0}fieldset{border:0;padding:0;min-width:0}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:500px;overflow:auto}.error{color:#dc2626}.create label{flex:1}.draft-actions{justify-content:flex-start;flex-wrap:wrap}.pending{background:#fff7ed;padding:12px;color:#9a3412}@media(max-width:900px){.scene-layout{grid-template-columns:1fr}header,.create{flex-wrap:wrap}}
</style>
