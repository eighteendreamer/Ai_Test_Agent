---
name: Vue Testing Utils
description: 官方 Vue.js 测试工具,用于组件挂载、模拟和断言
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [unit]
frameworks: [jest]
languages: [typescript, javascript]
domains: [web]
info: vip.hctestedu.com
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# Vue Testing Utils

此技能使 AI 代理使用 `@vue/test-utils` 在 Vitest 上编写 Vue 3 组件测试:使用 props 和 slots 挂载、通过 `data-testid` 查询、触发事件并等待渲染队列、断言 `emitted()` 载荷,以及通过 `global.plugins` 连接 `createTestingPinia` 和模拟路由器。在任何需要单元或集成测试的 Vue 3 + Vite 项目中触发。

## 核心原则

1. **默认使用 `mount`,很少使用 `shallowMount`。** stub 所有子级测试一个骨架,而不是组件。浅渲染仅当子级确实很重(图表、地图)时使用 — 显式 stub 那个子级,而不是所有子级。
2. **测试渲染契约:props in,DOM 和 emitted 事件 out。** 永远不要深入 `wrapper.vm` 内部或断言 `ref` 值;那些测试只是偶然地通过重构存活。
3. **`await` 每个交互。** Vue 批量 DOM 更新;`trigger`、`setValue` 和 `setProps` 都返回在下一个 tick 后解析的 promise。缺少 `await` 对陈旧 DOM 进行断言。
4. **使用 `data-testid` 或 roles,而不是类选择器。** Tailwind/scoped-CSS 类随样式工作变化;测试 ID 仅在行为变化时变化。
5. **emitted 事件是组件的 API — 断言名称和载荷。** `wrapper.emitted('save')` 返回调用数组;检查它是否触发以及携带了什么。
6. **真实的 Pinia 逻辑,假的服务器。** 使用 `createTestingPinia({ stubActions: false })` 加上模拟 HTTP,你诚实地测试 store-component 集成;stubbed actions 仅用于纯渲染测试。

## 设置

```bash
npm install --save-dev @vue/test-utils vitest jsdom @pinia/testing @vitejs/plugin-vue
```

```typescript
// vitest.config.ts
import vue from '@vitejs/plugin-vue';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [vue()],
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.ts'],
    restoreMocks: true,
  },
});
```

一个值得测试的组件:

```vue
<!-- src/components/QuantityPicker.vue -->
<script setup lang="ts">
import { computed } from 'vue';

const props = withDefaults(defineProps<{ modelValue: number; max?: number }>(), { max: 10 });
const emit = defineEmits<{ 'update:modelValue': [value: number] }>();

const atMax = computed(() => props.modelValue >= props.max);

function increment(): void {
  if (!atMax.value) emit('update:modelValue', props.modelValue + 1);
}
</script>

<template>
  <div>
    <span data-testid="qty">{{ modelValue }}</span>
    <button data-testid="inc" :disabled="atMax" @click="increment">+</button>
  </div>
</template>
```

测试:

```typescript
// src/components/QuantityPicker.test.ts
import { mount } from '@vue/test-utils';
import { describe, expect, it } from 'vitest';
import QuantityPicker from './QuantityPicker.vue';

describe('QuantityPicker', () => {
  it('emits update:modelValue with the incremented quantity', async () => {
    const wrapper = mount(QuantityPicker, { props: { modelValue: 2 } });

    await wrapper.find('[data-testid="inc"]').trigger('click');

    expect(wrapper.emitted('update:modelValue')).toEqual([[3]]);
  });

  it('disables the button at max and emits nothing on click', async () => {
    const wrapper = mount(QuantityPicker, { props: { modelValue: 5, max: 5 } });
    const button = wrapper.find('[data-testid="inc"]');

    expect(button.attributes('disabled')).toBeDefined();
    await button.trigger('click');
    expect(wrapper.emitted('update:modelValue')).toBeUndefined();
  });

  it('re-renders when the parent updates the prop', async () => {
    const wrapper = mount(QuantityPicker, { props: { modelValue: 1 } });
    await wrapper.setProps({ modelValue: 7 });
    expect(wrapper.get('[data-testid="qty"]').text()).toBe('7');
  });
});
```

## 模式

### 使用 setValue 和 v-model 的表单

```typescript
import { mount } from '@vue/test-utils';
import { expect, it } from 'vitest';
import LoginForm from './LoginForm.vue';

it('submits trimmed credentials as the submit event payload', async () => {
  const wrapper = mount(LoginForm);

  await wrapper.get('[data-testid="email"]').setValue('  mira@example.com ');
  await wrapper.get('[data-testid="password"]').setValue('hunter2hunter2');
  await wrapper.get('form').trigger('submit.prevent');

  expect(wrapper.emitted('submit')).toEqual([
    [{ email: 'mira@example.com', password: 'hunter2hunter2' }],
  ]);
});
```

### 异步组件:flushPromises 后模拟 Fetch

```typescript
import { flushPromises, mount } from '@vue/test-utils';
import { expect, it, vi } from 'vitest';
import SkillList from './SkillList.vue';
import * as api from '../api/skills';

it('renders fetched skills after the loading state', async () => {
  vi.spyOn(api, 'fetchSkills').mockResolvedValue([
    { slug: 'vitest-testing', name: 'Vitest' },
    { slug: 'msw-mocking', name: 'MSW' },
  ]);

  const wrapper = mount(SkillList);
  expect(wrapper.find('[data-testid="spinner"]').exists()).toBe(true);

  await flushPromises(); // 解析 fetch 和后续渲染

  expect(wrapper.find('[data-testid="spinner"]').exists()).toBe(false);
  expect(wrapper.findAll('[data-testid="skill-row"]')).toHaveLength(2);
  expect(wrapper.text()).toContain('Vitest');
});
```

### Pinia Stores 与 createTestingPinia

```typescript
import { createTestingPinia } from '@pinia/testing';
import { mount } from '@vue/test-utils';
import { expect, it, vi } from 'vitest';
import CartBadge from './CartBadge.vue';
import { useCartStore } from '../stores/cart';

it('shows the item count from the store and calls clear on click', async () => {
  const wrapper = mount(CartBadge, {
    global: {
      plugins: [
        createTestingPinia({
          createSpy: vi.fn,
          initialState: { cart: { items: [{ sku: 'A1' }, { sku: 'B2' }] } },
        }),
      ],
    },
  });
  const store = useCartStore(); // 组件使用的相同实例

  expect(wrapper.get('[data-testid="count"]').text()).toBe('2');

  await wrapper.get('[data-testid="clear"]').trigger('click');
  expect(store.clear).toHaveBeenCalledOnce(); // actions 自动 stub 为 spies
});
```

### 路由器:模拟它,而不是挂载它

```typescript
import { mount } from '@vue/test-utils';
import { expect, it, vi } from 'vitest';
import SkillCard from './SkillCard.vue';

it('navigates to the skill detail page on card click', async () => {
  const push = vi.fn();
  const wrapper = mount(SkillCard, {
    props: { slug: 'supertest-api', name: 'SuperTest' },
    global: {
      mocks: { $router: { push } },
      stubs: { RouterLink: { template: '<a><slot /></a>' } },
    },
  });

  await wrapper.get('[data-testid="card"]').trigger('click');

  expect(push).toHaveBeenCalledWith({ name: 'skill-detail', params: { slug: 'supertest-api' } });
});
```

### 插槽和作用域插槽

```typescript
import { mount } from '@vue/test-utils';
import { expect, it } from 'vitest';
import DataTable from './DataTable.vue';

it('renders the scoped row slot with each item', () => {
  const wrapper = mount(DataTable, {
    props: { items: [{ id: 1, name: 'alpha' }] },
    slots: {
      row: `<template #row="{ item }"><td data-testid="cell">{{ item.name }}</td></template>`,
    },
  });

  expect(wrapper.get('[data-testid="cell"]').text()).toBe('alpha');
});
```

## 最佳实践

- 当元素必须存在时使用 `wrapper.get()`(抛出清晰消息);`wrapper.find()` + `.exists()` 仅在断言不存在时使用。
- 通过工厂共享挂载默认值:`const factory = (props = {}) => mount(Comp, { props: { ...defaults, ...props } })` — 而不是通过可变模块级 wrapper。
- 在整个调用数组上断言 `emitted()` 载荷相等性(`toEqual([[3]])`)以免费捕获双重触发。
- 对于使用 `<Teleport>` 的组件,使用 `document.querySelector` 定位 teleport 目标或使用 `global.stubs: { teleport: true }` stub teleport。
- 测试与可访问性相关的输出:`attributes('aria-expanded')`、`attributes('disabled')` — 这些是行为,而不是样式。
- 每个测试文件一个组件,每个测试新鲜挂载;`restoreMocks: true` 加上新鲜挂载消除 90% 的跨测试污染。

## 反模式

- **`wrapper.vm.someRef = 5` 设置状态。** 改变内部绕过组件契约;通过 props、交互或 store 初始状态驱动状态。
- **在 `trigger`/`setValue`/`setProps` 上缺少 `await`。** 断言看到上一个 DOM 并出于错误原因通过或失败。
- **`shallowMount` 作为各地的默认值。** 快照中的 stub 名称(`<child-component-stub>`)什么都不验证集成。
- **为单元测试挂载完整真实路由器并等待 `router.isReady()`。** 模拟 `$router.push` 代替;真实路由器测试属于小的专用导航套件。
- **将 CSS 类作为行为断言** (`expect(wrapper.classes()).toContain('text-red-500')`)。断言驱动类的状态(`aria-invalid`、发出的验证事件)而不是。
- **一个 `beforeEach` 为文件中的每个测试挂载 kitchen-sink 全局配置** — 插槽、store 和路由器配置应该出现在需要它们的测试中。

## 何时触发此技能

- Vue 3 项目需要组件测试,或 `@vue/test-utils` 在 `devDependencies` 中。
- 用户询问如何测试 props、emits、v-model、slots 或 Vue 组件中的异步数据获取。
- 测试因缺少 `await`/`flushPromises` 而不稳定或通过共享 wrapper 相互污染。
- 组件依赖 Pinia 或 Vue Router,用户需要它们在测试中被模拟或 stub。
- 将 Vue 2(`createLocalVue`、`propsData`)测试迁移到 Vue 3 `global`/`props` API。