/* 树的模型都在 SDK，网页这边只转发：
   ① skeleton.ts：拓扑 → 每条线的几何与时长
   ② growth.ts：骨架 + 出生时刻 → 某一帧的枝/叶进度与叶片位姿
   ③ runtime.ts：唯一外界入口，内部保存拓扑、出生、分叉与结束历史

   组件只提交 TreeInput、读取 TreeFrame。取景、配色、hover 和落体轨迹是呈现；
   它们不再另存一套生长历史。验收脚本直接执行这些产品函数，不抄副本。 */
export {
  buildGrowth,
  leafAt,
  DEFAULT_GROWTH,
  LEAF_UNIT,
  LEAF_W,
  type Blade,
  type GStem,
  type GrowthNode,
  type GrowthOptions,
  type Pt,
  type Skeleton,
} from '../../../../routine-atlas/src/lib/skeleton'

export {
  BEAT,
  anyGrowing,
  birthTime,
  bornTimes,
  easeGrow,
  finishedAt,
  grownFor,
  leafFrame,
  type LeafFrame,
  rawP,
  sincePast,
  stemP,
} from '../../../../routine-atlas/src/lib/growth'

export {
  TreeRuntime,
  type DepartedStem,
  type TreeEvent,
  type TreeFrame,
  type TreeInput,
  type TurnedStem,
} from '../../../../routine-atlas/src/lib/runtime'

/* 老名字。`GrowthModel` 在 SDK 里叫 `Skeleton` —— 那个结构里只有几何，
   叫「模型」会让人以为它也管时间；改名的过渡期留着这个别名 */
export type { Skeleton as GrowthModel } from '../../../../routine-atlas/src/lib/skeleton'
