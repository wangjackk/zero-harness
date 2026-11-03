import { createRouter, createWebHashHistory, type RouteRecordRaw } from 'vue-router'
import { EDGES } from './data/edges'
import { TOTAL_NAMES } from './data/names'

export type RouteGroup = 'core' | 'harness' | 'deep'
export interface Chapter {
  path: string; title: string; en: string; no: string; blurb: string; group: RouteGroup
}
export const ROUTE_GROUPS: { id: RouteGroup; label: string; en: string }[] = [
  { id: 'core', label: 'Routine Core', en: '通用执行与异构协作' },
  { id: 'harness', label: 'Agent Harness', en: '智能体应用骨架' },
  { id: 'deep', label: '深入系统', en: '工程实现与注册检索' }
]

export const ROUTES: Chapter[] = [
  { path: '/', title: '概览', en: 'Overview', no: '01', group: 'core', blurb: 'Routine Core、Agent Harness 与异构网络的边界' },
  { path: '/anatomy', title: '解剖', en: 'Anatomy', no: '02', group: 'core', blurb: '单元职责与命令实例生命周期' },
  { path: '/modules', title: '模块系统', en: 'Module System', no: '03', group: 'core', blurb: '模块树、占用声明、冲突检测与优先级仲裁' },
  { path: '/communication', title: '通信', en: 'Communication', no: '04', group: 'core', blurb: '单元通信与调用 · 多设备 Hub 发布、订阅与跨端调用' },
  { path: '/society', title: '协作', en: 'Routine Society', no: '05', group: 'core', blurb: '人、异构 Agent、工具、服务与外部接口' },
  { path: '/evolution', title: '演进', en: 'Evolution', no: '06', group: 'core', blurb: '经验 · 规程 · 模型 · 混合编排' },
  { path: '/topology', title: '拓扑', en: 'Topology', no: '07', group: 'core', blurb: `${TOTAL_NAMES} 个单元，${EDGES.length} 条关系` },
  { path: '/bootstrap', title: '自举', en: 'Bootstrap & Self-iteration', no: '08', group: 'core', blurb: '系统自举、热加载与能力自迭代的通用闭环' },
  { path: '/harness', title: 'Harness', en: 'Agent Harness', no: '09', group: 'harness', blurb: 'Harness 的六个特点，及其在 Routine 系统里的实现' },
  { path: '/assemble', title: '装配', en: 'Assembly', no: '10', group: 'harness', blurb: '最小智能体结构与行动轨迹' },
  { path: '/robot', title: '机器人', en: 'Embodied Agent', no: '11', group: 'harness', blurb: '流式 Agent、共享 Routine 身体与设备连接' },
  { path: '/engineering', title: '工程实现', en: 'Engineering Notes', no: '12', group: 'deep', blurb: 'Loader、实时语音、Agent、DAG 与 Skill 的实现档案' },
  { path: '/registry', title: '清单', en: 'Registry', no: '13', group: 'deep', blurb: `${TOTAL_NAMES} 个注册名及声明` }
]

export const PAGE_TOTAL = String(ROUTES.length).padStart(2, '0')
export function pageNo(path: string): string {
  const r = ROUTES.find(x => x.path === path)
  return `${r?.no ?? '—'} / ${PAGE_TOTAL}`
}

const routes: RouteRecordRaw[] = [
  { path: '/', name: 'overview', component: () => import('./pages/Overview.vue') },
  { path: '/anatomy', name: 'anatomy', component: () => import('./pages/Anatomy.vue') },
  { path: '/modules', name: 'modules', component: () => import('./pages/Modules.vue') },
  { path: '/communication', name: 'communication', component: () => import('./pages/Communicate.vue') },
  { path: '/society', name: 'society', component: () => import('./pages/Society.vue') },
  { path: '/evolution', name: 'evolution', component: () => import('./pages/Evolve.vue') },
  { path: '/topology', name: 'topology', component: () => import('./pages/Topology.vue') },
  { path: '/harness', name: 'harness', component: () => import('./pages/Harness.vue') },
  { path: '/assemble', name: 'assemble', component: () => import('./pages/Assemble.vue') },
  { path: '/robot', name: 'robot', component: () => import('./pages/Robot.vue') },
  { path: '/engineering', name: 'engineering', component: () => import('./pages/Engineering.vue') },
  { path: '/registry', name: 'registry', component: () => import('./pages/Registry.vue') },
  { path: '/bootstrap', name: 'bootstrap', component: () => import('./pages/Bootstrap.vue') },
  /* 调参台：不进导航，只给树算法看形态用 */
  { path: '/tree', name: 'tree-lab', component: () => import('./pages/TreeLab.vue') },
  { path: '/life', name: 'tree-life', component: () => import('./pages/Life.vue') },
  { path: '/:pathMatch(.*)*', redirect: '/' }
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
  scrollBehavior(to, from, saved) {
    if (saved) return saved
    if (to.hash) return { el: to.hash, top: 90, behavior: 'smooth' }
    return { top: 0 }
  }
})

export default router
