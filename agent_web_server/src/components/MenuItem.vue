<template>
  <div class="menu-item" :class="{ open: isOpen }">
    <!-- 无子菜单的顶级项 -->
    <router-link
      v-if="!menu.children && menu.path"
      :to="menu.path"
      class="flex items-center gap-3 px-6 py-4 cursor-pointer hover:bg-white/5 transition-all no-expand"
      :class="{ 'active-item': currentPath === menu.path }"
    >
      <i :class="['fas', menu.icon, 'w-5 text-center opacity-80']"></i>
      <span class="font-medium">{{ menu.label }}</span>
    </router-link>
    
    <!-- 有子菜单的展开项 -->
    <template v-else>
      <div
        class="flex items-center justify-between px-6 py-4 cursor-pointer hover:bg-white/5 transition-all"
        @click="$emit('toggle', menu.id)"
      >
        <div class="flex items-center gap-3">
          <i :class="['fas', menu.icon, 'w-5 text-center opacity-80']"></i>
          <span class="font-medium">{{ menu.label }}</span>
        </div>
        <i class="fas fa-chevron-down text-xs chevron transition-transform opacity-50"></i>
      </div>
      <div class="menu-content flex flex-col">
        <!-- 二级菜单项 -->
        <template v-for="child in menu.children" :key="child.id || child.path">
          <!-- 二级菜单：无子菜单（直接跳转） -->
          <router-link
            v-if="!child.children && child.path"
            :to="child.path"
            class="sub-item pl-14 pr-6 py-3 text-sm text-left opacity-70 hover:opacity-100 transition-all"
            :class="{ active: currentPath === child.path }"
          >
            {{ child.label }}
          </router-link>
          
          <!-- 二级菜单：有子菜单（三级展开） -->
          <div v-else class="sub-menu-group">
            <div
              class="sub-item pl-14 pr-6 py-3 text-sm text-left opacity-70 hover:opacity-100 transition-all cursor-pointer flex items-center justify-between"
              @click.stop="$emit('toggle-sub', child.id)"
            >
              <span>{{ child.label }}</span>
              <i class="fas fa-chevron-right text-xs sub-chevron transition-transform" :class="{ 'rotate-90': openSubMenus.includes(child.id) }"></i>
            </div>
            <!-- 三级菜单 -->
            <div class="third-level-content" :class="{ open: openSubMenus.includes(child.id) }">
              <router-link
                v-for="grandChild in child.children"
                :key="grandChild.path"
                :to="grandChild.path"
                class="third-item pl-20 pr-6 py-2.5 text-xs text-left opacity-60 hover:opacity-100 transition-all"
                :class="{ active: currentPath === grandChild.path }"
              >
                {{ grandChild.label }}
              </router-link>
            </div>
          </div>
        </template>
      </div>
    </template>
  </div>
</template>

<script setup>
defineProps({
  menu: {
    type: Object,
    required: true
  },
  isOpen: {
    type: Boolean,
    default: false
  },
  currentPath: {
    type: String,
    default: ''
  },
  openSubMenus: {
    type: Array,
    default: () => []
  }
})

defineEmits(['toggle', 'toggle-sub'])
</script>

<style scoped>
.menu-content {
  max-height: 0;
  overflow: hidden;
  opacity: 0;
  transform: translateY(-4px);
  transition:
    max-height 0.28s cubic-bezier(0.25, 0.8, 0.25, 1),
    opacity 0.22s ease-out,
    transform 0.28s ease-out;
  background: rgba(0, 0, 0, 0.15);
}

.menu-item.open .menu-content {
  max-height: 1200px;
  opacity: 1;
  transform: translateY(0);
}

.menu-item.open .chevron {
  transform: rotate(180deg);
}

.sub-item {
  text-decoration: none;
  color: inherit;
  display: block;
  transform: translateY(2px);
  transition:
    color 0.2s ease-out,
    background-color 0.2s ease-out,
    opacity 0.2s ease-out,
    transform 0.24s ease-out;
}

.menu-item.open .sub-item {
  transform: translateY(0);
}

.sub-item.active {
  background-color: white;
  color: #007857;
  font-weight: 600;
  opacity: 1;
}

/* 三级菜单样式 */
.third-level-content {
  max-height: 0;
  overflow: hidden;
  opacity: 0;
  transform: translateY(-2px);
  transition:
    max-height 0.25s cubic-bezier(0.25, 0.8, 0.25, 1),
    opacity 0.2s ease-out,
    transform 0.25s ease-out;
  background: rgba(0, 0, 0, 0.2);
}

.third-level-content.open {
  max-height: 500px;
  opacity: 1;
  transform: translateY(0);
}

.third-item {
  text-decoration: none;
  color: inherit;
  display: block;
  transition:
    color 0.2s ease-out,
    background-color 0.2s ease-out,
    opacity 0.2s ease-out;
}

.third-item.active {
  background-color: rgba(255, 255, 255, 0.95);
  color: #007857;
  font-weight: 600;
  opacity: 1;
}

.sub-chevron {
  transition: transform 0.2s ease-out;
}

.no-expand {
  text-decoration: none;
  color: inherit;
}

.active-item {
  background-color: rgba(255, 255, 255, 0.15);
}
</style>
