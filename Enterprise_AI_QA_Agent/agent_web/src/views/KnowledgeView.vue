<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from "vue";
import { NButton, NModal } from "naive-ui";

import { api } from "../services/api";
import { t } from "../services/i18n";
import { formatServerDateTime } from "../utils/datetime";
import type {
  KnowledgeGraphEdge,
  KnowledgeGraphNode,
  KnowledgeGraphResponse,
  KnowledgeProjectSummary,
} from "../types";

type NodeKindFilter = "all" | "page" | "entity" | "element";

const CANVAS_WIDTH = 840;
const CANVAS_HEIGHT = 500;

const loadingProjects = ref(false);
const loadingGraph = ref(false);
const errorMessage = ref("");
const projects = ref<KnowledgeProjectSummary[]>([]);
const selectedProjectId = ref("");
const graph = ref<KnowledgeGraphResponse | null>(null);
const searchTerm = ref("");
const activeKind = ref<NodeKindFilter>("all");
const activeNodeId = ref("");
const detailDrawerOpen = ref(false);
const deletingProject = ref(false);
const deleteDialogOpen = ref(false);
const dialog = { warning: (_options: unknown) => undefined };

const canvasViewportRef = ref<SVGSVGElement | null>(null);
const canvasCardRef = ref<HTMLElement | null>(null);
const canvasZoom = ref(1);
const canvasOffset = ref({ x: 0, y: 0 });
const isDraggingCanvas = ref(false);
const isFullscreenCanvas = ref(false);
const dragPointerId = ref<number | null>(null);
const dragStartPoint = ref({ x: 0, y: 0 });
const dragStartOffset = ref({ x: 0, y: 0 });

const selectedProject = computed(() =>
  projects.value.find((item) => item.project_id === selectedProjectId.value) ?? null,
);

const nodesById = computed(() => {
  const map = new Map<string, KnowledgeGraphNode>();
  for (const node of graph.value?.nodes ?? []) {
    map.set(node.id, node);
  }
  return map;
});

const filteredNodes = computed(() => {
  const keyword = searchTerm.value.trim().toLowerCase();
  return (graph.value?.nodes ?? []).filter((node) => {
    if (activeKind.value !== "all" && node.kind !== activeKind.value) {
      return false;
    }
    if (!keyword) {
      return true;
    }
    const haystack = `${node.label} ${node.summary} ${JSON.stringify(node.metadata)}`.toLowerCase();
    return haystack.includes(keyword);
  });
});

const selectedNode = computed(() => {
  if (activeNodeId.value === '__GLOBAL_PAGES__') {
    return null;
  }
  const firstNode = filteredNodes.value[0] ?? graph.value?.nodes?.[0] ?? null;
  if (!activeNodeId.value && firstNode) {
    activeNodeId.value = firstNode.id;
  }
  return nodesById.value.get(activeNodeId.value) ?? firstNode;
});

const selectedNodeEdges = computed(() => {
  const nodeId = selectedNode.value?.id;
  if (!nodeId) {
    return [] as KnowledgeGraphEdge[];
  }
  return (graph.value?.edges ?? []).filter((edge) => edge.source === nodeId || edge.target === nodeId);
});

const neighborNodes = computed(() => {
  const nodeId = selectedNode.value?.id;
  if (!nodeId) {
    return [] as KnowledgeGraphNode[];
  }
  const ids = new Set<string>();
  for (const edge of selectedNodeEdges.value) {
    ids.add(edge.source === nodeId ? edge.target : edge.source);
  }
  return Array.from(ids)
    .map((id) => nodesById.value.get(id))
    .filter((node): node is KnowledgeGraphNode => Boolean(node))
    .slice(0, 8);
});

const relationChips = computed(() =>
  Object.entries(graph.value?.summary.relation_counts ?? {}).sort((left, right) => right[1] - left[1]),
);

const adjacencyByNodeId = computed(() => {
  const map = new Map<string, KnowledgeGraphEdge[]>();
  for (const edge of graph.value?.edges ?? []) {
    const sourceEdges = map.get(edge.source) ?? [];
    sourceEdges.push(edge);
    map.set(edge.source, sourceEdges);

    if (edge.target !== edge.source) {
      const targetEdges = map.get(edge.target) ?? [];
      targetEdges.push(edge);
      map.set(edge.target, targetEdges);
    }
  }
  return map;
});

const canvasNodes = computed(() => {
  if (activeNodeId.value === '__GLOBAL_PAGES__') {
    const pages = (graph.value?.nodes ?? []).filter(n => n.kind === 'page');
    const items = [
      {
        id: '__GLOBAL_PAGES__',
        label: selectedProject.value?.project_scope || t('knowledge.project_global'),
        kind: 'project',
        x: 420,
        y: 250,
        center: true,
        level: 0,
      },
    ];

    const visited = new Set<string>();
    const firstRingRadius = Math.max(180, pages.length * 22);
    
    pages.forEach((node, index) => {
      visited.add(node.id);
      const angle = (-Math.PI / 2) + (Math.PI * 2 * index) / Math.max(pages.length, 1);
      items.push({
        id: node.id,
        label: node.label,
        kind: node.kind,
        x: 420 + Math.cos(angle) * firstRingRadius,
        y: 250 + Math.sin(angle) * firstRingRadius,
        center: false,
        level: 1,
      });

      const children = (adjacencyByNodeId.value.get(node.id) ?? [])
        .map(edge => edge.source === node.id ? edge.target : edge.source)
        .filter(id => !visited.has(id))
        .map(id => nodesById.value.get(id))
        .filter((n): n is KnowledgeGraphNode => Boolean(n));
      
      const displayChildren = children.slice(0, 6);
      const secondRingRadius = firstRingRadius + 160;
      const spread = displayChildren.length === 1 ? [0] : Array.from({length: displayChildren.length}, (_, i) => -0.4 + (0.8 * i / (displayChildren.length - 1)));
      
      displayChildren.forEach((child, cIdx) => {
        visited.add(child.id);
        const childAngle = angle + spread[cIdx];
        items.push({
          id: child.id,
          label: child.label,
          kind: child.kind,
          x: 420 + Math.cos(childAngle) * secondRingRadius,
          y: 250 + Math.sin(childAngle) * secondRingRadius,
          center: false,
          level: 2,
        });

        const grandChildren = (adjacencyByNodeId.value.get(child.id) ?? [])
          .map(edge => edge.source === child.id ? edge.target : edge.source)
          .filter(id => !visited.has(id))
          .map(id => nodesById.value.get(id))
          .filter((n): n is KnowledgeGraphNode => Boolean(n));
        
        const displayGrand = grandChildren.slice(0, 4);
        const thirdRingRadius = secondRingRadius + 140;
        const grandSpread = displayGrand.length === 1 ? [0] : Array.from({length: displayGrand.length}, (_, i) => -0.25 + (0.5 * i / (displayGrand.length - 1)));
        
        displayGrand.forEach((grand, gIdx) => {
          visited.add(grand.id);
          const grandAngle = childAngle + grandSpread[gIdx];
          items.push({
            id: grand.id,
            label: grand.label,
            kind: grand.kind,
            x: 420 + Math.cos(grandAngle) * thirdRingRadius,
            y: 250 + Math.sin(grandAngle) * thirdRingRadius,
            center: false,
            level: 3,
          });
        });
      });
    });
    return items;
  }

  const center = selectedNode.value;
  if (!center) {
    return [] as Array<{
      id: string;
      label: string;
      kind: string;
      x: number;
      y: number;
      center: boolean;
      level: number;
    }>;
  }

  const items = [
    {
      id: center.id,
      label: center.label,
      kind: center.kind,
      x: 420,
      y: 250,
      center: true,
      level: 0,
    },
  ];

  const visited = new Set<string>([center.id]);
  const firstRingRadius = 170;
  const secondRingRadius = 300;

  neighborNodes.value.forEach((node, index) => {
    visited.add(node.id);
    const angle = (-Math.PI / 2) + (Math.PI * 2 * index) / Math.max(neighborNodes.value.length, 1);
    const x = 420 + Math.cos(angle) * firstRingRadius;
    const y = 250 + Math.sin(angle) * firstRingRadius;

    items.push({
      id: node.id,
      label: node.label,
      kind: node.kind,
      x,
      y,
      center: false,
      level: 1,
    });

    const secondRingCandidates = (adjacencyByNodeId.value.get(node.id) ?? [])
      .map((edge) => (edge.source === node.id ? edge.target : edge.source))
      .filter((neighborId) => neighborId !== center.id && !visited.has(neighborId))
      .map((neighborId) => nodesById.value.get(neighborId))
      .filter((candidate): candidate is KnowledgeGraphNode => Boolean(candidate))
      .slice(0, 2);

    const spread = secondRingCandidates.length === 1 ? [0] : [-0.26, 0.26];
    secondRingCandidates.forEach((candidate, candidateIndex) => {
      visited.add(candidate.id);
      const childAngle = angle + spread[candidateIndex];
      items.push({
        id: candidate.id,
        label: candidate.label,
        kind: candidate.kind,
        x: 420 + Math.cos(childAngle) * secondRingRadius,
        y: 250 + Math.sin(childAngle) * secondRingRadius,
        center: false,
        level: 2,
      });
    });
  });

  return items;
});

const canvasNodeMap = computed(() => {
  const map = new Map<string, (typeof canvasNodes.value)[number]>();
  for (const node of canvasNodes.value) {
    map.set(node.id, node);
  }
  return map;
});

const canvasEdges = computed(() => {
  let baseEdges = graph.value?.edges ?? [];
  if (activeNodeId.value === '__GLOBAL_PAGES__') {
    const pages = (graph.value?.nodes ?? []).filter(n => n.kind === 'page');
    const virtualEdges = pages.map(p => ({
      id: `root_to_${p.id}`,
      source: '__GLOBAL_PAGES__',
      target: p.id,
      label: t('knowledge.contains_page'),
      metadata: {}
    } as KnowledgeGraphEdge));
    baseEdges = [...baseEdges, ...virtualEdges];
  }

  return baseEdges
    .map((edge) => {
      const source = canvasNodeMap.value.get(edge.source);
      const target = canvasNodeMap.value.get(edge.target);
      if (!source || !target) {
        return null;
      }
      return { ...edge, sourceNode: source, targetNode: target };
    })
    .filter((edge): edge is NonNullable<typeof edge> => Boolean(edge));
});

const canvasTransform = computed(
  () => `translate(${canvasOffset.value.x} ${canvasOffset.value.y}) scale(${canvasZoom.value})`,
);

watch([filteredNodes, activeKind], ([nodes]) => {
  if (activeNodeId.value === '__GLOBAL_PAGES__') {
    return;
  }
  if (!nodes.length) {
    activeNodeId.value = "";
    detailDrawerOpen.value = false;
    return;
  }
  if (!nodes.some((node) => node.id === activeNodeId.value)) {
    activeNodeId.value = nodes[0].id;
  }
});

watch(selectedProjectId, async (projectId) => {
  if (!projectId) {
    graph.value = null;
    return;
  }
  await loadGraph(projectId);
});

onMounted(async () => {
  document.addEventListener("fullscreenchange", syncFullscreenState);
  await loadProjects();
});

onBeforeUnmount(() => {
  document.removeEventListener("fullscreenchange", syncFullscreenState);
});

async function loadProjects() {
  loadingProjects.value = true;
  errorMessage.value = "";
  try {
    const response = await api.listKnowledgeProjects();
    projects.value = response;
    if (!selectedProjectId.value && response.length > 0) {
      selectedProjectId.value = response[0].project_id || "";
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "Failed to load knowledge projects";
  } finally {
    loadingProjects.value = false;
  }
}

async function loadGraph(projectId: string) {
  loadingGraph.value = true;
  errorMessage.value = "";
  try {
    const response = await api.getKnowledgeGraph(projectId);
    graph.value = response;
    activeNodeId.value = '__GLOBAL_PAGES__';
    detailDrawerOpen.value = false;
    resetCanvasView();
  } catch (error) {
    graph.value = null;
    activeNodeId.value = "";
    detailDrawerOpen.value = false;
    errorMessage.value = error instanceof Error ? error.message : "Failed to load project graph";
  } finally {
    loadingGraph.value = false;
  }
}

function openDeleteProjectDialog() {
  const projectId = selectedProjectId.value.trim();
  if (!projectId || deletingProject.value) {
    return;
  }
  deleteDialogOpen.value = true;
}

function cancelDeleteProjectDialog() {
  if (deletingProject.value) {
    return;
  }
  deleteDialogOpen.value = false;
}

async function confirmDeleteProjectDialog() {
  const projectId = selectedProjectId.value.trim();
  if (!projectId || deletingProject.value) {
    return;
  }

  deletingProject.value = true;
  errorMessage.value = "";
  try {
    await api.deleteKnowledgeProject(projectId);
    const nextProjectId = projects.value.find((item) => item.project_id !== projectId)?.project_id ?? "";
    graph.value = null;
    activeNodeId.value = "";
    detailDrawerOpen.value = false;
    selectedProjectId.value = "";
    deleteDialogOpen.value = false;
    await loadProjects();
    if (nextProjectId) {
      selectedProjectId.value = nextProjectId;
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "Failed to delete project graph";
  } finally {
    deletingProject.value = false;
  }
}

async function deleteSelectedProject() {
  const projectId = selectedProjectId.value.trim();
  if (!projectId || deletingProject.value) {
    return;
  }
  dialog.warning({
    title: t("knowledge.confirm_delete_title"),
    content: `确认删除项目图谱“${scope}”吗？{{ t("knowledge.confirm_delete_warning") }}`,
    positiveText: t("common.confirm"),
    negativeText: t("common.cancel"),
    maskClosable: false,
    onPositiveClick: async () => {
      deletingProject.value = true;
      errorMessage.value = "";
      try {
        await api.deleteKnowledgeProject(projectId);
        const nextProjectId = projects.value.find((item) => item.project_id !== projectId)?.project_id ?? "";
        graph.value = null;
        activeNodeId.value = "";
        detailDrawerOpen.value = false;
        selectedProjectId.value = "";
        await loadProjects();
        if (nextProjectId) {
          selectedProjectId.value = nextProjectId;
        }
      } catch (error) {
        errorMessage.value = error instanceof Error ? error.message : "Failed to delete project graph";
        throw error;
      } finally {
        deletingProject.value = false;
      }
    },
  });
  return;
  const confirmed = window.confirm(t("knowledge.confirm_delete_content", { scope }));
  if (!confirmed) {
    return;
  }

  deletingProject.value = true;
  errorMessage.value = "";
  try {
    await api.deleteKnowledgeProject(scope);
    const nextScope =
      projects.value.find((item) => item.project_id !== projectId)?.project_id ?? "";
    graph.value = null;
    activeNodeId.value = "";
    detailDrawerOpen.value = false;
    selectedProjectId.value = "";
    await loadProjects();
    if (nextScope) {
      selectedProjectId.value = nextProjectId;
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "Failed to delete project graph";
  } finally {
    deletingProject.value = false;
  }
}

function kindLabel(kind: string) {
  if (kind === "page") return "Page";
  if (kind === "entity") return "Entity";
  if (kind === "element") return "Element";
  return kind;
}

function formatTime(value?: string | null) {
  if (!value) {
    return "--";
  }
  return formatServerDateTime(value, "--");
}

function truncateText(value: unknown, max = 72) {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  if (!text) {
    return "--";
  }
  if (text.length <= max) {
    return text;
  }
  return `${text.slice(0, max)}...`;
}

function selectNode(nodeId: string) {
  activeNodeId.value = nodeId;
  if (nodeId === '__GLOBAL_PAGES__') {
    detailDrawerOpen.value = false;
  } else {
    detailDrawerOpen.value = true;
  }
}

function closeDetailDrawer() {
  detailDrawerOpen.value = false;
}

function clampZoom(value: number) {
  return Math.min(2.4, Math.max(0.45, value));
}

function zoomCanvas(delta: number) {
  canvasZoom.value = clampZoom(canvasZoom.value + delta);
}

function resetCanvasView() {
  canvasZoom.value = 1;
  canvasOffset.value = { x: 0, y: 0 };
}

function startCanvasDrag(event: PointerEvent) {
  if (event.button !== 0) {
    return;
  }
  const target = event.target;
  if (target instanceof Element && target.closest(".graph-node")) {
    return;
  }
  isDraggingCanvas.value = true;
  dragPointerId.value = event.pointerId;
  dragStartPoint.value = { x: event.clientX, y: event.clientY };
  dragStartOffset.value = { ...canvasOffset.value };
  canvasViewportRef.value?.setPointerCapture(event.pointerId);
}

function moveCanvasDrag(event: PointerEvent) {
  if (!isDraggingCanvas.value || dragPointerId.value !== event.pointerId || !canvasViewportRef.value) {
    return;
  }
  const bounds = canvasViewportRef.value.getBoundingClientRect();
  if (!bounds.width || !bounds.height) {
    return;
  }
  const dx = ((event.clientX - dragStartPoint.value.x) / bounds.width) * CANVAS_WIDTH;
  const dy = ((event.clientY - dragStartPoint.value.y) / bounds.height) * CANVAS_HEIGHT;
  canvasOffset.value = {
    x: dragStartOffset.value.x + dx / canvasZoom.value,
    y: dragStartOffset.value.y + dy / canvasZoom.value,
  };
}

function endCanvasDrag(event?: PointerEvent) {
  if (event && dragPointerId.value === event.pointerId) {
    canvasViewportRef.value?.releasePointerCapture(event.pointerId);
  }
  isDraggingCanvas.value = false;
  dragPointerId.value = null;
}

function handleCanvasWheel(event: WheelEvent) {
  event.preventDefault();
  zoomCanvas(event.deltaY < 0 ? 0.12 : -0.12);
}

async function toggleCanvasFullscreen() {
  if (!canvasCardRef.value) {
    return;
  }
  if (document.fullscreenElement === canvasCardRef.value) {
    await document.exitFullscreen();
    return;
  }
  await canvasCardRef.value.requestFullscreen();
}

function syncFullscreenState() {
  isFullscreenCanvas.value = document.fullscreenElement === canvasCardRef.value;
}
</script>

<template>
  <section class="view-page knowledge-page">
    <div class="page-head knowledge-head">
      <div>
        <h2>{{ t("knowledge.page_title") }}</h2>
        <p class="head-desc">{{ t("knowledge.page_desc") }}</p>
      </div>
    </div>

    <div class="knowledge-layout">
      <aside class="knowledge-sidebar">
        <div class="sidebar-card">
          <div class="sidebar-head">
            <h3>{{ t("knowledge.project_filter") }}</h3>
            <span class="sidebar-meta">{{ projects.length }} {{ t("knowledge.projects_count_unit") }}</span>
          </div>
          <div class="project-actions">
          <select v-model="selectedProjectId" class="knowledge-select" :disabled="loadingProjects || !projects.length">
            <option value="" disabled>{{ t("knowledge.select_project") }}</option>
            <option v-for="project in projects" :key="project.project_id || project.project_scope" :value="project.project_id || ''">
              {{ project.project_scope }}
            </option>
          </select>
            <button
              class="icon-btn danger-btn"
              type="button"
              :title="t('knowledge.delete_project')"
              :disabled="!selectedProjectId || deletingProject"
              @click="openDeleteProjectDialog"
            >
              <i class="fa-solid fa-trash"></i>
            </button>
          </div>
          <div v-if="selectedProject" class="project-meta">
            <div><span>{{ t("knowledge.pages") }}</span><strong>{{ selectedProject.page_count }}</strong></div>
            <div><span>{{ t("knowledge.elements") }}</span><strong>{{ selectedProject.element_count }}</strong></div>
            <div><span>{{ t("knowledge.entities") }}</span><strong>{{ selectedProject.entity_count }}</strong></div>
            <div><span>{{ t("knowledge.relations") }}</span><strong>{{ selectedProject.edge_count }}</strong></div>
          </div>
        </div>

        <div class="sidebar-card">
          <div class="sidebar-head">
            <h3>{{ t("knowledge.node_filter") }}</h3>
            <span class="sidebar-meta">{{ filteredNodes.length }} {{ t("knowledge.nodes_count_unit") }}</span>
          </div>
          <div class="knowledge-kind-tabs">
            <button
              v-for="kind in ['all', 'page', 'entity', 'element']"
              :key="kind"
              class="kind-tab"
              :class="{ active: activeKind === kind }"
              type="button"
              @click="activeKind = kind as NodeKindFilter"
            >
              {{ kind === "all" ? t("knowledge.filter_all") : kindLabel(kind) }}
            </button>
          </div>
          <input v-model="searchTerm" class="knowledge-search" type="text" :placeholder="t('knowledge.search_nodes_placeholder')" />
          <div class="node-list">
            <button
              class="node-row global-row"
              :class="{ active: activeNodeId === '__GLOBAL_PAGES__' }"
              type="button"
              @click="selectNode('__GLOBAL_PAGES__')"
            >
              <span class="node-kind" data-kind="project">Global</span>
              <span class="node-main">
                <strong>{{ t("knowledge.global_pages_graph") }}</strong>
                <small>{{ t("knowledge.global_pages_desc") }}</small>
              </span>
            </button>
            <button
              v-for="node in filteredNodes"
              :key="node.id"
              class="node-row"
              :class="{ active: selectedNode?.id === node.id }"
              type="button"
              @click="selectNode(node.id)"
            >
              <span class="node-kind" :data-kind="node.kind">{{ kindLabel(node.kind) }}</span>
              <span class="node-main">
                <strong :title="node.label">{{ truncateText(node.label, 30) }}</strong>
                <small :title="node.summary || node.id">{{ truncateText(node.summary || node.id, 56) }}</small>
              </span>
            </button>
            <div v-if="!filteredNodes.length" class="empty-state small">{{ t("knowledge.no_nodes_filtered") }}</div>
          </div>
        </div>
      </aside>

      <main class="knowledge-main">
        <section ref="canvasCardRef" class="canvas-card" :class="{ fullscreen: isFullscreenCanvas }">
          <div class="canvas-head">
            <div>
              <h3>{{ t("knowledge.project_graph") }}</h3>
            </div>
            <div class="canvas-head-right">
              <div class="canvas-toolbar">
                <button class="icon-btn" type="button" :title="t('knowledge.zoom_out')" @click="zoomCanvas(-0.12)">
                  <i class="fa-solid fa-minus"></i>
                </button>
                <button class="icon-btn" type="button" :title="t('knowledge.zoom_reset')" @click="resetCanvasView">
                  <i class="fa-solid fa-arrows-rotate"></i>
                </button>
                <button class="icon-btn" type="button" :title="t('knowledge.zoom_in')" @click="zoomCanvas(0.12)">
                  <i class="fa-solid fa-plus"></i>
                </button>
                <button class="icon-btn" type="button" :title="isFullscreenCanvas ? t('knowledge.exit_fullscreen') : t('knowledge.enter_fullscreen')" @click="toggleCanvasFullscreen">
                  <i class="fa-solid" :class="isFullscreenCanvas ? 'fa-compress' : 'fa-expand'"></i>
                </button>
              </div>
              <div class="canvas-meta">
                <span>{{ t("knowledge.updated_at") }}{{ formatTime(graph?.summary.latest_updated_at) }}</span>
              </div>
            </div>
          </div>

          <div v-if="loadingGraph" class="empty-state">{{ t("knowledge.loading_graph") }}</div>
          <div v-else-if="errorMessage" class="empty-state danger">{{ errorMessage }}</div>
          <div v-else-if="!graph" class="empty-state">{{ t("knowledge.select_project_hint") }}</div>
          <div v-else class="canvas-wrap">
            <svg
              ref="canvasViewportRef"
              viewBox="0 0 840 500"
              class="graph-canvas"
              :class="{ dragging: isDraggingCanvas }"
              role="img"
              aria-label="Knowledge graph preview"
              @pointerdown="startCanvasDrag($event)"
              @pointermove="moveCanvasDrag($event)"
              @pointerup="endCanvasDrag($event)"
              @pointerleave="endCanvasDrag($event)"
              @wheel="handleCanvasWheel($event)"
            >
              <g :transform="canvasTransform">
                <line
                  v-for="edge in canvasEdges"
                  :key="edge.id"
                  :x1="edge.sourceNode.x"
                  :y1="edge.sourceNode.y"
                  :x2="edge.targetNode.x"
                  :y2="edge.targetNode.y"
                  class="graph-edge"
                />
                <g
                  v-for="node in canvasNodes"
                  :key="node.id"
                  class="graph-node"
                  :class="[node.kind, { active: activeNodeId === node.id }]"
                  @click.stop="selectNode(node.id)"
                >
                  <circle :cx="node.x" :cy="node.y" :r="node.center ? 34 : node.level === 1 ? 24 : 18" />
                  <text :x="node.x" :y="node.y + (node.center ? 54 : node.level === 1 ? 42 : 34)" text-anchor="middle">
                    {{ node.label.length > 14 ? `${node.label.slice(0, 14)}...` : node.label }}
                  </text>
                </g>
              </g>
            </svg>

            <div class="relation-chip-row">
              <span v-for="[name, count] in relationChips" :key="name" class="relation-chip">
                {{ name }} · {{ count }}
              </span>
            </div>
          </div>
        </section>
      </main>
    </div>

    <transition name="drawer-fade">
      <div v-if="detailDrawerOpen" class="drawer-backdrop" @click="closeDetailDrawer"></div>
    </transition>
    <transition name="drawer-slide">
      <aside v-if="detailDrawerOpen" class="detail-drawer">
        <div class="detail-drawer-head">
          <div>
            <h3>{{ t("knowledge.node_detail") }}</h3>
            <span class="sidebar-meta">{{ selectedNode ? kindLabel(selectedNode.kind) : t("knowledge.not_selected") }}</span>
          </div>
          <button class="icon-btn" type="button" @click="closeDetailDrawer" :aria-label="t('knowledge.close_detail_drawer')">
            <i class="fa-solid fa-xmark"></i>
          </button>
        </div>

        <div v-if="selectedNode" class="detail-content">
          <div class="detail-title">
            <strong :title="selectedNode.label">{{ truncateText(selectedNode.label, 48) }}</strong>
            <small :title="selectedNode.summary || selectedNode.id">
              {{ truncateText(selectedNode.summary || selectedNode.id, 120) }}
            </small>
          </div>

          <div class="detail-section">
            <h4>{{ t("knowledge.adjacent_relations") }}</h4>
            <div class="detail-list">
              <div v-for="edge in selectedNodeEdges" :key="edge.id" class="detail-list-item">
                <span class="relation-tag">{{ edge.label }}</span>
                <span
                  class="relation-target"
                  :title="
                    edge.source === selectedNode.id
                      ? nodesById.get(edge.target)?.label ?? edge.target
                      : nodesById.get(edge.source)?.label ?? edge.source
                  "
                >
                  {{
                    truncateText(
                      edge.source === selectedNode.id
                        ? nodesById.get(edge.target)?.label ?? edge.target
                        : nodesById.get(edge.source)?.label ?? edge.source,
                      56,
                    )
                  }}
                </span>
              </div>
            </div>
          </div>

          <div class="detail-section">
            <h4>{{ t("knowledge.properties") }}</h4>
            <div class="detail-list">
              <div
                v-for="entry in Object.entries(selectedNode.metadata)"
                :key="entry[0]"
                class="detail-list-item property-item"
              >
                <span class="property-key">{{ entry[0] }}</span>
                <span
                  class="property-value"
                  :title="typeof entry[1] === 'object' ? JSON.stringify(entry[1]) : String(entry[1])"
                >
                  {{ truncateText(typeof entry[1] === 'object' ? JSON.stringify(entry[1]) : entry[1], 96) }}
                </span>
              </div>
              <div v-if="!Object.keys(selectedNode.metadata).length" class="empty-state small">
                {{ t("knowledge.no_properties") }}
              </div>
            </div>
          </div>
        </div>
        <div v-else class="empty-state">{{ t("knowledge.select_node_hint") }}</div>
      </aside>
    </transition>

    <NModal
      :show="deleteDialogOpen"
      preset="card"
      class="knowledge-delete-modal"
      :style="{ width: 'min(460px, calc(100vw - 32px))' }"
      :mask-closable="!deletingProject"
      :closable="false"
      @update:show="(value) => { if (!value) cancelDeleteProjectDialog(); }"
    >
      <div class="delete-modal-head">
        <div class="delete-modal-title">{{ t("knowledge.confirm_delete_title") }}</div>
      </div>
      <div class="delete-modal-body">
        <p>
          {{ t("knowledge.confirm_delete_ask", { scope: selectedProject?.project_scope || selectedProjectId }) }}
        </p>
        <p>{{ t("knowledge.confirm_delete_warning") }}</p>
      </div>
      <div class="delete-modal-actions">
        <NButton class="delete-modal-cancel" quaternary :disabled="deletingProject" @click="cancelDeleteProjectDialog">{{ t("common.cancel") }}</NButton>
        <NButton class="delete-modal-confirm" :loading="deletingProject" @click="confirmDeleteProjectDialog">{{ t("common.confirm") }}</NButton>
      </div>
    </NModal>
  </section>
</template>

<style scoped>
.knowledge-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 16px;
}

.knowledge-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 0;
  padding-bottom: 8px;
  border-bottom: 1px solid var(--border);
}

.knowledge-head h2 {
  font-size: 20px;
  font-weight: 600;
  margin: 0 0 2px 0;
}

.knowledge-head .head-desc {
  font-size: 12px;
  color: var(--muted);
  margin: 0;
}

.knowledge-layout {
  display: grid;
  grid-template-columns: 320px minmax(0, 1fr);
  gap: 16px;
  min-height: 0;
  flex: 1;
}

.knowledge-sidebar,
.knowledge-main {
  min-height: 0;
}

.knowledge-sidebar {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.sidebar-card,
.canvas-card,
.summary-card {
  border: 1px solid var(--border);
  background: var(--surface);
  border-radius: 12px;
  box-shadow: 0 2px 12px rgba(0, 0, 0, 0.03);
}

.sidebar-card,
.canvas-card {
  padding: 12px 16px;
}

.canvas-card {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.canvas-card.fullscreen {
  width: 100vw;
  height: 100vh;
  border-radius: 0;
  padding: 20px;
  box-sizing: border-box;
}

.zsidebar-head,
.canvas-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
}

.sidebar-head h3,
.canvas-head h3 {
  margin: 0;
  font-size: 15px;
}

.canvas-head-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.canvas-toolbar {
  display: flex;
  align-items: center;
  gap: 8px;
}

.sidebar-meta,
.canvas-meta,
.detail-title small,
.node-main small,
.empty-state.small {
  color: var(--muted);
}

.knowledge-select,
.knowledge-search {
  width: 100%;
  height: 40px;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 0 12px;
  background: var(--surface-soft);
  color: var(--text);
  outline: none;
  transition: all 0.2s;
}

.knowledge-select {
  appearance: none;
  flex: 1;
  min-width: 0;
  background-image: url("data:image/svg+xml;charset=UTF-8,%3csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%2364748b' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3e%3cpolyline points='6 9 12 15 18 9'%3e%3c/polyline%3e%3c/svg%3e");
  background-repeat: no-repeat;
  background-position: right 12px center;
  background-size: 16px;
  padding-right: 36px;
  cursor: pointer;
  text-overflow: ellipsis;
  white-space: nowrap;
  overflow: hidden;
}

.knowledge-select:focus,
.knowledge-search:focus {
  border-color: rgba(17, 24, 39, 0.4);
  box-shadow: 0 0 0 3px rgba(17, 24, 39, 0.08);
  background: var(--surface);
}

.knowledge-select:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.project-actions {
  display: flex;
  gap: 10px;
  align-items: center;
}

.danger-btn {
  color: #ef4444;
  border: 1px solid rgba(239, 68, 68, 0.2);
  background: rgba(239, 68, 68, 0.05);
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: 8px;
  transition: all 0.2s;
  cursor: pointer;
  flex-shrink: 0;
}

.danger-btn:hover:not(:disabled) {
  background: #ef4444;
  color: white;
  border-color: #ef4444;
  box-shadow: 0 4px 12px rgba(239, 68, 68, 0.2);
}

.danger-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
  filter: grayscale(1);
}

:deep(.knowledge-delete-modal) {
  width: min(460px, calc(100vw - 32px));
}

:deep(.knowledge-delete-modal .n-card) {
  border-radius: 16px;
  border: 1px solid var(--border);
  background: var(--surface);
  box-shadow: var(--shadow-panel);
}

:deep(.knowledge-delete-modal .n-card__content) {
  padding: 22px 24px 20px;
}

.delete-modal-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}

.delete-modal-title {
  font-size: 20px;
  font-weight: 700;
  color: var(--text);
  letter-spacing: -0.01em;
}

.delete-modal-body {
  color: var(--muted);
  font-size: 15px;
  line-height: 1.7;
}

.delete-modal-body p {
  margin: 0;
}

.delete-modal-body p + p {
  margin-top: 6px;
}

.delete-modal-body strong {
  color: var(--text);
  font-weight: 700;
}

.delete-modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 22px;
}

.delete-modal-cancel:deep(.n-button) {
  color: var(--text);
}

.delete-modal-cancel:deep(.n-button__border) {
  border-color: var(--border);
}

.delete-modal-cancel:deep(.n-button__state-border) {
  border-color: var(--border-strong);
}

.delete-modal-confirm:deep(.n-button) {
  background: var(--accent);
  color: var(--bg);
}

.delete-modal-confirm:deep(.n-button__border),
.delete-modal-confirm:deep(.n-button__state-border) {
  border-color: var(--accent);
}

.project-meta {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px dashed var(--border);
}

.project-meta div {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  min-width: 0;
  background: var(--surface-soft);
  padding: 8px 4px;
  border-radius: 8px;
}

.project-meta span {
  color: var(--muted);
  font-size: 11px;
}

.project-meta strong {
  font-size: 18px;
  font-weight: 600;
  line-height: 1;
}

.knowledge-kind-tabs {
  display: flex;
  background: var(--surface-soft);
  padding: 4px;
  border-radius: 8px;
  margin-bottom: 12px;
}

.kind-tab {
  flex: 1;
  border: none;
  background: transparent;
  color: var(--muted);
  border-radius: 6px;
  padding: 6px 0;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s;
  text-align: center;
}

.kind-tab.active {
  background: var(--surface);
  color: var(--text);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08);
}

.node-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-top: 16px;
  max-height: 330px;
  overflow: auto;
  padding-right: 4px;
}

.node-list::-webkit-scrollbar {
  width: 4px;
}
.node-list::-webkit-scrollbar-thumb {
  background: var(--border);
  border-radius: 4px;
}

.node-row {
  display: grid;
  grid-template-columns: 64px minmax(0, 1fr);
  gap: 12px;
  align-items: center;
  width: 100%;
  text-align: left;
  padding: 10px;
  border: 1px solid transparent;
  background: transparent;
  border-radius: 8px;
  transition: all 0.2s;
  cursor: pointer;
}

.node-row:hover {
  background: var(--surface-soft);
}

.node-row.active {
  background: rgba(17, 24, 39, 0.04);
  border-color: rgba(17, 24, 39, 0.2);
  box-shadow: 0 2px 6px rgba(17, 24, 39, 0.05);
}

.node-kind {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 6px;
  padding: 4px;
  font-size: 10px;
  font-weight: 600;
  background: var(--surface);
  border: 1px solid var(--border);
}

.node-kind[data-kind="page"] {
  color: #111827;
}

.node-kind[data-kind="entity"] {
  color: #059669;
}

.node-kind[data-kind="element"] {
  color: #d97706;
}

.node-kind[data-kind="project"] {
  color: #6366f1;
  border-color: rgba(99, 102, 241, 0.2);
}

.global-row {
  border: 1px dashed var(--border);
  margin-bottom: 8px;
  background: var(--surface-soft);
}
.global-row.active {
  border-style: solid;
}

.node-main {
  display: flex;
  flex-direction: column;
  min-width: 0;
  gap: 2px;
}

.node-main strong,
.node-main small {
  display: -webkit-box;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.node-main strong {
  -webkit-line-clamp: 2;
}

.node-main small {
  -webkit-line-clamp: 2;
}

.canvas-wrap {
  display: flex;
  flex-direction: column;
  gap: 14px;
  flex: 1;
  min-height: 0;
}

.graph-canvas {
  width: 100%;
  min-height: 540px;
  flex: 1;
  border: 1px solid var(--border);
  border-radius: 12px;
  background:
    radial-gradient(circle at center, rgba(17, 24, 39, 0.03), transparent 55%),
    var(--surface-soft);
  touch-action: none;
  cursor: grab;
}

.graph-canvas.dragging {
  cursor: grabbing;
}

.graph-edge {
  stroke: rgba(100, 116, 139, 0.35);
  stroke-width: 1.4;
}

.graph-node {
  cursor: pointer;
}

.graph-node circle {
  fill: var(--surface);
  stroke-width: 3;
}

.graph-node.page circle {
  stroke: #111827;
}

.graph-node.entity circle {
  stroke: #059669;
}

.graph-node.element circle {
  stroke: #0d9488;
}

.graph-node.project circle {
  stroke: #6366f1;
}

.graph-node.active circle {
  filter: drop-shadow(0 0 8px rgba(17, 24, 39, 0.18));
}

.graph-node text {
  fill: var(--text);
  font-size: 12px;
  font-weight: 600;
}

.relation-chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.relation-chip,
.relation-tag {
  display: inline-flex;
  align-items: center;
  border-radius: 999px;
  padding: 4px 10px;
  font-size: 11px;
  font-weight: 700;
  background: rgba(17, 24, 39, 0.08);
  color: #111827;
}

.detail-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.detail-title {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.detail-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.detail-section h4 {
  margin: 0;
  font-size: 13px;
}

.detail-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.detail-list-item {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 8px;
  align-items: start;
  padding: 10px 12px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: var(--surface-soft);
}

.property-item {
  grid-template-columns: 120px minmax(0, 1fr);
}

.property-key {
  color: var(--muted);
  font-size: 12px;
}

.property-value,
.relation-target {
  overflow-wrap: anywhere;
  line-height: 1.45;
}

.empty-state {
  min-height: 120px;
  display: flex;
  align-items: center;
  justify-content: center;
  text-align: center;
  border: 1px dashed var(--border);
  border-radius: 12px;
  color: var(--muted);
  background: var(--surface-soft);
  padding: 16px;
}

.empty-state.danger {
  color: #b91c1c;
  border-color: rgba(185, 28, 28, 0.24);
  background: rgba(185, 28, 28, 0.05);
}

.drawer-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.28);
  z-index: 40;
}

.detail-drawer {
  position: fixed;
  top: 0;
  right: 0;
  bottom: 0;
  width: min(420px, calc(100vw - 24px));
  background: var(--surface);
  border-left: 1px solid var(--border);
  box-shadow: -12px 0 32px rgba(15, 23, 42, 0.16);
  z-index: 41;
  display: flex;
  flex-direction: column;
  padding: 18px 16px 16px;
  gap: 16px;
  overflow: hidden;
}

.detail-drawer-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.detail-drawer-head h3 {
  margin: 0 0 4px;
  font-size: 18px;
}

.detail-drawer .detail-content,
.detail-drawer > .empty-state {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding-right: 4px;
}

.drawer-fade-enter-active,
.drawer-fade-leave-active {
  transition: opacity 0.2s ease;
}

.drawer-fade-enter-from,
.drawer-fade-leave-to {
  opacity: 0;
}

.drawer-slide-enter-active,
.drawer-slide-leave-active {
  transition: transform 0.24s ease;
}

.drawer-slide-enter-from,
.drawer-slide-leave-to {
  transform: translateX(100%);
}

@media (max-width: 1400px) {
  .knowledge-layout {
    grid-template-columns: 280px minmax(0, 1fr);
  }
}

@media (max-width: 1024px) {
  .knowledge-layout {
    grid-template-columns: 1fr;
  }
}
</style>
