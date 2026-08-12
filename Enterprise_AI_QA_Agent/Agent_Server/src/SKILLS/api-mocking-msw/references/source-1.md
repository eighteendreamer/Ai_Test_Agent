---
name: MSW API Mocking
description: Mock Service Worker 用于在浏览器和 Node.js 环境中无缝 API 模拟
version: 1.0.0
author: thetestingacademy
license: MIT
testingTypes: [unit, integration]
frameworks: [jest]
info: vip.hctestedu.com
languages: [typescript, javascript]
domains: [web, api]
agents: [claude-code, cursor, github-copilot, windsurf, codex, aider, continue, cline, zed, bolt]
---

# MSW API 模拟

此技能使 AI 代理在网络级别模拟 HTTP 和 GraphQL API,使用 Mock Service Worker v2:一组在 Vitest/Jest(通过 `setupServer`)和浏览器(通过 `setupWorker`)之间共享的请求处理器,带有 `server.use` 的按测试覆盖,以及 `onUnhandledRequest: 'error'` 策略以捕获漂移。当组件或服务在测试中调用 `fetch`/axios、当 `msw` 出现在 package.json 中,或当用户手动 stub `global.fetch` 并因此受苦时触发。

## 核心原则

1. **模拟网络,而不是模块。** `vi.mock('./api-client')` 将测试耦合到导入路径并跳过序列化、查询字符串和状态处理。MSW 拦截实际请求,因此整个客户端堆栈(拦截器、重试、解析)保持在测试之下。
2. **一个 `handlers.ts` 是契约。** 定义一次快乐路径处理器;测试、Storybook 和本地开发都使用相同的数组。当真实 API 更改时,你更新一个文件,每个消费者都会注意到。
3. **全局处理器中的快乐路径,每个测试中的失败。** 默认处理器返回现实的成功响应。错误情况(`500`、`422`、超时)通过 `server.use(...)` 在需要它们的测试内部声明,这会前置一次性覆盖。
4. **`onUnhandledRequest: 'error'` 始终。** 任何没有处理器的请求都应该大声失败。静默通过是"单元"测试最终从 CI 点击生产的方式。
5. **每个测试后重置处理器。** `server.resetHandlers()` 在 `afterEach` 中删除按测试的覆盖;没有它,测试顺序开始重要,套件会腐烂。
6. **使用现实的形状和状态码响应。** 使用与真实 API 返回的相同字段名、 casing、分页信封和错误体;漂移的 mock 教你的代码处理一个不存在的 API。

## 设置

```bash
npm install --save-dev msw
# 仅浏览器使用:将 worker 脚本放置在你的静态目录中
npx msw init public/ --save
```

### 共享处理器

```ts
// src/mocks/handlers.ts
import { http, HttpResponse, delay } from 'msw';

export interface User {
  id: string;
  name: string;
  role: 'admin' | 'member';
}

export const handlers = [
  http.get('https://api.example.com/users/:id', ({ params }) => {
    return HttpResponse.json<User>({
      id: String(params.id),
      name: 'Ada Lovelace',
      role: 'admin',
    });
  }),

  http.get('https://api.example.com/orders', ({ request }) => {
    const url = new URL(request.url);
    const page = Number(url.searchParams.get('page') ?? '1');
    return HttpResponse.json({
      items: [{ id: 'ord_1', total: 4999 }],
      page,
      totalPages: 3,
    });
  }),

  http.post('https://api.example.com/orders', async ({ request }) => {
    const body = (await request.json()) as { sku?: string; qty?: number };
    if (!body.sku) {
      return HttpResponse.json({ error: 'sku is required' }, { status: 422 });
    }
    await delay(50); // 模拟现实延迟
    return HttpResponse.json({ orderId: 'ord_2', ...body }, { status: 201 });
  }),
];
```

## 模式

### 1. Node 测试设置(Vitest 或 Jest)

```ts
// src/mocks/server.ts
import { setupServer } from 'msw/node';
import { handlers } from './handlers';

export const server = setupServer(...handlers);
```

```ts
// vitest.setup.ts (通过 vitest.config.ts 中的 test.setupFiles 注册)
import { beforeAll, afterEach, afterAll } from 'vitest';
import { server } from './src/mocks/server';

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());
```

### 2. 测试组件,然后覆盖失败情况

```tsx
// src/components/UserProfile.test.tsx
import { render, screen } from '@testing-library/react';
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { UserProfile } from './UserProfile';

it('renders the user fetched from the API', async () => {
  render(<UserProfile id="42" />);
  expect(await screen.findByRole('heading', { name: 'Ada Lovelace' })).toBeInTheDocument();
});

it('shows an error banner when the API is down', async () => {
  // 一次性覆盖;afterEach 中的 resetHandlers() 将其删除
  server.use(
    http.get('https://api.example.com/users/:id', () =>
      HttpResponse.json({ message: 'internal error' }, { status: 500 }),
    ),
  );

  render(<UserProfile id="42" />);
  expect(await screen.findByRole('alert')).toHaveTextContent('Could not load profile');
});

it('handles a network-level failure distinctly from a 500', async () => {
  server.use(
    http.get('https://api.example.com/users/:id', () => HttpResponse.error()),
  );

  render(<UserProfile id="42" />);
  expect(await screen.findByRole('alert')).toHaveTextContent('Check your connection');
});
```

### 3. 断言你代码发送的请求

```ts
// src/api/orders.test.ts
import { http, HttpResponse } from 'msw';
import { server } from '../mocks/server';
import { createOrder } from './orders';

it('sends the auth header and JSON body the API expects', async () => {
  let captured: { auth: string | null; body: unknown } | undefined;

  server.use(
    http.post('https://api.example.com/orders', async ({ request }) => {
      captured = {
        auth: request.headers.get('authorization'),
        body: await request.json(),
      };
      return HttpResponse.json({ orderId: 'ord_9' }, { status: 201 });
    }),
  );

  await createOrder({ sku: 'SKU-1', qty: 2 }, { token: 'jwt-abc' });

  expect(captured?.auth).toBe('Bearer jwt-abc');
  expect(captured?.body).toEqual({ sku: 'SKU-1', qty: 2 });
});
```

### 4. GraphQL 操作

```ts
// src/mocks/graphql-handlers.ts
import { graphql, HttpResponse } from 'msw';

export const gqlHandlers = [
  graphql.query('GetCart', ({ variables }) => {
    return HttpResponse.json({
      data: {
        cart: { id: variables.cartId, items: [{ sku: 'SKU-1', qty: 1 }] },
      },
    });
  }),

  graphql.mutation('AddToCart', () => {
    return HttpResponse.json({
      errors: [{ message: 'Out of stock', extensions: { code: 'OUT_OF_STOCK' } }],
    });
  }),
];
```

### 5. 用于开发和 Storybook 的浏览器 worker

```ts
// src/mocks/browser.ts
import { setupWorker } from 'msw/browser';
import { handlers } from './handlers';

export const worker = setupWorker(...handlers);
```

```ts
// src/main.tsx -- 仅在开发中启用模拟
async function enableMocking(): Promise<void> {
  if (!import.meta.env.DEV) return;
  const { worker } = await import('./mocks/browser');
  await worker.start({ onUnhandledRequest: 'bypass' });
}

enableMocking().then(() => {
  createRoot(document.getElementById('root')!).render(<App />);
});
```

## 最佳实践

- 键入你的响应体(`HttpResponse.json<User>(...)`)以便当应用的类型更改时,mock 漂移成为编译错误。
- 在支持加载状态测试的处理器中使用 `delay()`;0ms 响应可能在 React 渲染我们正在断言的微调器之前解析。
- 在处理器中保留路径参数(`:id`)和 `URL` 查询解析,而不是每个精确 URL 一个处理器;更少的处理器,更广泛的覆盖。
- 对于分页端点,从 `searchParams` 驱动响应,以便相同的处理器为第 1 页和第 7 页测试服务。
- 在 Jest(不是 Vitest)中,根据需要每个 MSW 文档进行 polyfill,并通过 `setupFilesAfterEach`/`setupFilesAfterEach` 等效项注册设置文件(setupFilesAfterEach 是 Vitest;Jest 使用 setupFilesAfterEach? 小心使用 setupFilesAfterEach) - 具体来说:通过 `setupFiles: ['<rootDir>/jest.setup.ts']` 以及相同的 listen/reset/close 三重奏。
- 将一次性覆盖与需要它们的测试并置;如果三个测试需要相同的失败处理器,将其提升为 `handlers.ts` 中的命名导出。

## 反模式

- Stub `global.fetch = vi.fn()` 并手工制作 `Response` 对象:脆弱的,跳过 URL 匹配,并且在你切换到 axios 的那一天死亡。
- 测试中的 `onUnhandledRequest: 'bypass'`:未模拟的调用静默到达真实服务,使测试缓慢、不稳定,偶尔具有破坏性。
- 全局定义错误情况处理器,以便每个测试从损坏的 API 开始并用覆盖"修复"它 - 反转它。
- 忘记 `afterEach` 中的 `server.resetHandlers()`,然后调试为什么 500 覆盖泄漏到接下来的十二个测试中。
- 在端到端测试中模拟你自己的服务器路由;MSW 用于单元/集成层,E2E 应该点击真实(容器化的)后端。
- 每个测试文件重复处理器数组并漂移分开;共享 `handlers.ts` 并在本地覆盖。

## 何时触发此技能

- 测试手动 stub `fetch`、`axios` 或 API 客户端模块,或者组件测试套件需要网络响应。
- `msw` 在 package.json 中,`mockServiceWorker.js` 在 `public/` 中,或者 `setupServer`/`setupWorker` 出现在代码库中。
- 用户要求"模拟 API"、"测试加载和错误状态"、"在测试和 Storybook 之间共享 mock"或"阻止测试点击真实 API"。
- GraphQL 客户端(Apollo、urql、graphql-request)需要按查询名称进行操作级模拟。
- 前端开发被未完成的后端阻止,需要一个现实的模拟层,以后作为测试 fixtures 的双重用途。